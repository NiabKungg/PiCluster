#!/usr/bin/env python3
"""Worker: registers with the master, sends heartbeats, and runs LLM
inference jobs.

Modes:
  real  -- llama-server must already be running on loopback (--port);
           each job is forwarded to it over HTTP and its timing stats
           are reported back (start llama-server yourself, e.g. under
           systemd -- see README)
  mock  -- no llama-server needed: sleeps and returns fake numbers so
           the whole pipeline can be tested without llama.cpp
"""

import argparse
import http.client
import json
import os
import secrets
import signal
import socket
import threading
import time

from common import MASTER_PORT, HEARTBEAT_PORT, recv_json, send_json

HEARTBEAT_INTERVAL = 2.0
LOOPBACK = "127.0.0.1"  # llama-server is contacted on loopback only


def log(msg):
    print(f"[worker {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def mem_total_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def llama_request(port, path, payload=None, timeout=1800):
    """GET/POST against the fixed loopback llama-server port."""
    conn = http.client.HTTPConnection(LOOPBACK, port, timeout=timeout)
    try:
        if payload is None:
            conn.request("GET", path)
        else:
            conn.request("POST", path, body=json.dumps(payload),
                         headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode())
    finally:
        conn.close()


class LlamaRunner:
    mode = "llama"

    def __init__(self, args):
        if not (1 <= args.port <= 65535):
            raise SystemExit(f"invalid port: {args.port}")
        self.port = int(args.port)

    def start(self):
        endpoint = f"http://{LOOPBACK}:{self.port}"
        log(f"waiting for llama-server at {endpoint} ...")
        refused = 0
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                status, _ = llama_request(self.port, "/health", timeout=3)
                if status == 200:
                    log("llama-server ready")
                    return
                refused = 0  # server is up, still loading the model: keep waiting
            except OSError:
                refused += 1
                if refused > 10:  # nothing is listening -> misconfiguration
                    raise SystemExit(
                        f"nothing listening on {endpoint}. Start llama-server first:\n"
                        f"  llama-server -m <model.gguf> --host {LOOPBACK} "
                        f"--port {self.port}\n"
                        "or install a systemd unit (see README).")
            time.sleep(0.5)
        raise SystemExit("llama-server did not become healthy within 300s")

    def run(self, job):
        status, resp = llama_request(self.port, "/completion", payload={
            "prompt": job["prompt"],
            "n_predict": int(job.get("max_tokens", 128)),
            "temperature": float(job.get("temperature", 0.0)),
            "seed": int(job.get("seed", 42))})
        if status != 200:
            return {"status": "error", "error": f"llama-server HTTP {status}"}
        timing = resp.get("timings", {})
        return {"status": "ok",
                "tokens_predicted": resp.get("tokens_predicted"),
                "predicted_per_second": timing.get("predicted_per_second"),
                "prompt_per_second": timing.get("prompt_per_second"),
                "output_text": (resp.get("content") or "")[:300]}

    def stop(self):
        pass


def _mock_range(lo, hi):
    """CSPRNG-backed uniform draw, used only for fake mock-mode numbers."""
    span = round((hi - lo) * 100)
    return round(lo + secrets.randbelow(span + 1) / 100.0, 2)


class MockRunner:
    mode = "mock"

    def start(self):
        log("mock mode: sleeping instead of running llama.cpp")

    def run(self, job):
        time.sleep(_mock_range(0.5, 2.0))
        return {"status": "ok",
                "tokens_predicted": int(job.get("max_tokens", 128)),
                "predicted_per_second": _mock_range(8, 14),
                "prompt_per_second": _mock_range(30, 60),
                "output_text": "MOCK OUTPUT"}

    def stop(self):
        pass


def heartbeat_loop(master, name, stop):
    """Keeps a dedicated connection open and pings the master every 2s."""
    while not stop.is_set():
        try:
            s = socket.create_connection((master, HEARTBEAT_PORT), timeout=5)
            while not stop.is_set():
                send_json(s, {"type": "HEARTBEAT", "name": name})
                stop.wait(HEARTBEAT_INTERVAL)
            s.close()
        except OSError:
            stop.wait(1.0)  # master briefly unreachable; retry


def main():
    ap = argparse.ArgumentParser(description="cluster worker")
    ap.add_argument("--master", required=True, help="master IP or hostname")
    ap.add_argument("--name", default=socket.gethostname())
    ap.add_argument("--port", type=int, default=8080,
                    help="loopback port of a running llama-server; use "
                         "distinct values when two real workers share one "
                         "machine")
    ap.add_argument("--mock", action="store_true",
                    help="skip llama.cpp entirely and fake the work")
    args = ap.parse_args()

    runner = MockRunner() if args.mock else LlamaRunner(args)
    runner.start()

    stop = threading.Event()
    threading.Thread(target=heartbeat_loop, args=(args.master, args.name, stop),
                     daemon=True).start()

    try:
        while True:  # reconnect forever: a lost master must not retire this worker
            conn = None
            while conn is None:
                try:
                    conn = socket.create_connection((args.master, MASTER_PORT),
                                                    timeout=5)
                except OSError:
                    log("waiting for master...")
                    time.sleep(1.0)
            conn.settimeout(None)  # job waits can be long; block until they arrive
            try:
                send_json(conn, {"type": "REGISTER", "name": args.name,
                                 "mode": runner.mode, "cpu": os.cpu_count(),
                                 "mem_mb": mem_total_mb()})
            except OSError:
                try:
                    conn.close()
                except OSError:
                    pass
                time.sleep(1.0)
                continue
            log(f"registered with master {args.master} as {args.name} "
                f"({runner.mode})")
            try:
                while True:
                    msg = recv_json(conn)
                    if msg.get("type") != "JOB":
                        continue
                    log(f"got job {msg['job_id']}")
                    try:
                        result = runner.run(msg)
                    except Exception as exc:  # report failure instead of hanging
                        result = {"status": "error", "error": str(exc)}
                    result["type"] = "JOB_RESULT"
                    send_json(conn, result)
            except (ConnectionError, OSError) as exc:
                log(f"connection lost ({exc}); reconnecting in 3s")
                time.sleep(3.0)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass
    except KeyboardInterrupt:
        log("disconnected")
    finally:
        stop.set()
        runner.stop()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
