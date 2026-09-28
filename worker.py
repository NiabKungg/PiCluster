#!/usr/bin/env python3
"""Worker: registers with the master, sends heartbeats with machine
telemetry, runs LLM inference jobs, and owns a llama-server child so
the master can command a model switch at runtime.

Modes:
  real  -- spawns llama-server locally (--model names a .gguf inside
           ~/models) and forwards each job to it over loopback HTTP;
           SET_MODEL switches to another .gguf from the same directory
  mock  -- no llama.cpp needed: sleeps and returns fake numbers so the
           whole pipeline can be tested without llama.cpp
"""

import argparse
import http.client
import json
import os
import re
import secrets
import signal
import socket
import threading
import time

from common import MASTER_PORT, HEARTBEAT_PORT, recv_json, send_json

HEARTBEAT_INTERVAL = 2.0
LOOPBACK = "127.0.0.1"  # llama-server is contacted on loopback only
MODELS_DIR = os.path.join(os.path.expanduser("~"), "models")
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.gguf$")


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


def mem_avail_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def cpu_temp_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def uptime_s():
    try:
        with open("/proc/uptime") as f:
            return round(float(f.read().split()[0]), 1)
    except (OSError, ValueError, IndexError):
        return None


def list_models(models_dir):
    try:
        return sorted(n for n in os.listdir(models_dir)
                      if MODEL_NAME_RE.match(n))
    except OSError:
        return []


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
    """Owns the llama-server child process; switches models on demand."""

    mode = "llama"

    def __init__(self, args):
        if not (1 <= args.port <= 65535):
            raise SystemExit(f"invalid port: {args.port}")
        self.port = int(args.port)
        self.ctx = int(args.ctx)
        self.threads = int(args.threads)
        self.models_dir = MODELS_DIR
        self.bin_path = os.path.abspath(args.llama_bin)
        if not (os.path.isfile(self.bin_path)
                and os.access(self.bin_path, os.X_OK)):
            raise SystemExit(
                f"llama binary not found/executable: {self.bin_path}")
        self.pid = None
        self.llama_ok = False
        self.current_model = None

    def _model_path(self, name):
        """Validate a model name into a file strictly inside MODELS_DIR."""
        if not MODEL_NAME_RE.match(name or ""):
            raise ValueError(f"invalid model name: {name!r}")
        real = os.path.realpath(os.path.join(self.models_dir, name))
        if not real.startswith(os.path.realpath(self.models_dir) + os.sep):
            raise ValueError("model escapes the models directory")
        if not os.path.isfile(real):
            raise FileNotFoundError(f"model not found: {name}")
        return real

    def _spawn(self, model_path):
        # argv array with a validated absolute binary path and a model
        # file resolved inside MODELS_DIR; nothing is ever passed to a
        # shell, and no network input reaches this call unvalidated.
        argv = [self.bin_path, "-m", model_path,
                "--host", LOOPBACK, "--port", str(self.port),
                "-c", str(self.ctx), "-t", str(self.threads)]
        log("starting llama-server: " + " ".join(argv))
        logf = open(os.path.join(os.path.expanduser("~"),
                                 "llama-server.log"), "ab")
        self.pid = os.posix_spawn(self.bin_path, argv, os.environ)

    def _wait_health(self, deadline_s):
        deadline = time.time() + deadline_s
        refused = 0
        while time.time() < deadline:
            try:
                done, _ = os.waitpid(self.pid, os.WNOHANG)
                if done == self.pid:
                    self.llama_ok = False
                    raise RuntimeError("llama-server exited during startup "
                                       "(see ~/llama-server.log)")
            except ChildProcessError:
                pass  # already reaped; health probe decides
            try:
                status, _ = llama_request(self.port, "/health", timeout=3)
                if status == 200:
                    self.llama_ok = True
                    return
                refused = 0  # up but still loading: keep waiting
            except OSError:
                refused += 1
                if refused > 20:
                    break
            time.sleep(0.5)
        self.llama_ok = False
        raise RuntimeError("llama-server did not become healthy")

    def start(self):
        name = os.path.basename(str(_ARGS.model))
        model_path = self._model_path(name)
        self._spawn(model_path)
        self._wait_health(300)
        self.current_model = os.path.basename(model_path)
        log(f"llama-server ready with {self.current_model}")

    def control(self, msg):
        """Apply a SET_MODEL command from the master."""
        name = str(msg.get("model", ""))
        try:
            model_path = self._model_path(name)
        except (ValueError, FileNotFoundError) as exc:
            return {"ok": False, "model": name, "error": str(exc)}
        if self.current_model == os.path.basename(model_path) and self.llama_ok:
            return {"ok": True, "model": self.current_model,
                    "changed": False}
        self.stop()
        try:
            self._spawn(model_path)
            self._wait_health(180)
        except (RuntimeError, OSError) as exc:
            return {"ok": False, "model": name, "error": str(exc)}
        self.current_model = os.path.basename(model_path)
        log(f"model switched to {self.current_model}")
        return {"ok": True, "model": self.current_model, "changed": True}

    def telemetry(self):
        return {"temp_c": cpu_temp_c(), "uptime_s": uptime_s(),
                "llama_ok": self.llama_ok,
                "mem_avail_mb": mem_avail_mb(),
                "models": list_models(self.models_dir),
                "current_model": self.current_model}

    def run(self, job):
        """Send the job through the model's CHAT template (the model is an
        instruct model — raw /completion prompts produce nonsense), via the
        OpenAI-compatible endpoint. Returns the reply plus timing stats."""
        payload = {
            "messages": [
                {"role": "system",
                 "content": "You are a helpful assistant running on a "
                            "Raspberry Pi cluster. Answer concisely."},
                {"role": "user", "content": str(job["prompt"])},
            ],
            "max_tokens": int(job.get("max_tokens", 128)),
            "temperature": float(job.get("temperature", 0.7)),
            "seed": int(job.get("seed", 42)),
            "stream": False,
        }
        status, resp = llama_request(self.port, "/v1/chat/completions",
                                     payload=payload)
        if status != 200:
            return {"status": "error", "error": f"llama-server HTTP {status}"}
        timing = resp.get("timings", {})
        usage = resp.get("usage", {})
        text = ""
        choices = resp.get("choices") or []
        if choices:
            text = ((choices[0].get("message") or {}).get("content")) or ""
        predicted = usage.get("completion_tokens")
        return {"status": "ok",
                "tokens_predicted": predicted,
                "predicted_per_second": timing.get("predicted_per_second"),
                "prompt_per_second": timing.get("prompt_per_second"),
                "output_text": text[:400]}

    def stop(self):
        if self.pid is not None:
            try:
                os.kill(self.pid, signal.SIGTERM)
                os.waitpid(self.pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
            self.pid = None


def _mock_range(lo, hi):
    """CSPRNG-backed uniform draw, used only for fake mock-mode numbers."""
    span = round((hi - lo) * 100)
    return round(lo + secrets.randbelow(span + 1) / 100.0, 2)


class MockRunner:
    mode = "mock"

    def start(self):
        log("mock mode: sleeping instead of running llama.cpp")

    def control(self, msg):
        return {"ok": True, "model": str(msg.get("model", "")),
                "changed": True, "mock": True}

    def telemetry(self):
        return {"temp_c": cpu_temp_c(), "uptime_s": uptime_s(),
                "llama_ok": True, "mem_avail_mb": mem_avail_mb(),
                "models": [], "current_model": "mock"}

    def run(self, job):
        time.sleep(_mock_range(0.5, 2.0))
        return {"status": "ok",
                "tokens_predicted": int(job.get("max_tokens", 128)),
                "predicted_per_second": _mock_range(8, 14),
                "prompt_per_second": _mock_range(30, 60),
                "output_text": "MOCK OUTPUT"}

    def stop(self):
        pass


def keepalive(sock):
    """Detect half-open connections (e.g. link flap that never delivered
    FIN/RST) within ~9s instead of blocking in recv() forever."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 5)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 2)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)


def heartbeat_loop(master, name, stop, runner, state):
    """Keeps a dedicated connection open and pings the master every 2s,
    attaching fresh machine telemetry to every beat. If two consecutive
    beats fail, the control socket is forcibly reset so the main loop
    reconnects (recovers from half-open TCP after link flaps)."""
    failures = 0
    while not stop.is_set():
        try:
            s = socket.create_connection((master, HEARTBEAT_PORT), timeout=5)
            failures = 0
            while not stop.is_set():
                payload = {"type": "HEARTBEAT", "name": name}
                payload.update(runner.telemetry())
                send_json(s, payload)
                failures = 0
                stop.wait(HEARTBEAT_INTERVAL)
            s.close()
        except OSError:
            failures += 1
            if failures >= 2:
                c = state.get("conn")
                if c is not None:
                    try:
                        c.shutdown(socket.SHUT_RDWR)  # wake the main recv
                    except OSError:
                        pass
            stop.wait(1.0)  # master briefly unreachable; retry


def main():
    ap = argparse.ArgumentParser(description="cluster worker")
    ap.add_argument("--master", required=True, help="master IP or hostname")
    ap.add_argument("--name", default=socket.gethostname())
    ap.add_argument("--model",
                    help=".gguf filename inside ~/models (real mode); "
                         "omit for mock mode")
    ap.add_argument("--llama-bin",
                    default=os.path.join(os.path.expanduser("~"),
                                         "llama.cpp", "build", "bin",
                                         "llama-server"),
                    help="path to the llama-server binary")
    ap.add_argument("--port", type=int, default=8080,
                    help="loopback port for this worker's llama-server; "
                         "use distinct values when two real workers share "
                         "one machine")
    ap.add_argument("--ctx", type=int, default=512)
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--mock", action="store_true",
                    help="skip llama.cpp entirely and fake the work")
    args = ap.parse_args()
    globals()["_ARGS"] = args

    if args.mock:
        runner = MockRunner()
    else:
        if not args.model:
            raise SystemExit("real mode requires --model <name.gguf> "
                             "(inside ~/models), or use --mock")
        runner = LlamaRunner(args)
    runner.start()

    stop = threading.Event()
    state = {"conn": None}
    threading.Thread(target=heartbeat_loop,
                     args=(args.master, args.name, stop, runner, state),
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
            keepalive(conn)
            conn.settimeout(None)  # job waits can be long; block until they arrive
            state["conn"] = conn
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
                    kind = msg.get("type")
                    if kind == "SET_MODEL":
                        result = runner.control(msg)
                        result["type"] = "MODEL_RESULT"
                        send_json(conn, result)
                        continue
                    if kind != "JOB":
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
                state["conn"] = None
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
    signal.signal(signal.SIGTERM, lambda *_: exit(0))
    main()
