#!/usr/bin/env python3
"""Master: accepts worker registrations, dispatches jobs to free workers,
monitors heartbeats, and requeues jobs from dead workers.

Message flow:
  worker -> master : REGISTER, JOB_RESULT
  worker -> master : HEARTBEAT (separate port, every 2s)
  master -> worker : JOB
  client -> master : SUBMIT
  master -> client : JOB_RESULT (streamed), DONE (summary)
"""

import collections
import itertools
import json
import queue
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common import MASTER_PORT, HEARTBEAT_PORT, recv_json, send_json

HEARTBEAT_TIMEOUT = 8.0  # seconds without a heartbeat -> worker assumed dead
MAX_ATTEMPTS = 3         # a job is executed at most this many times
IDLE_POLL = 0.1
STATUS_PORT = 5557       # read-only cluster API (GET /api/cluster, POST /api/model)
MODEL_NAME_RE = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.gguf$")


def log(msg):
    print(f"[master {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def listen(host, port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(16)
    return srv


class Job:
    _ids = itertools.count()

    def __init__(self, prompt, max_tokens, temperature, seed):
        self.id = next(Job._ids)
        self.prompt = prompt
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.seed = seed
        self.attempts = 0
        self.client = None
        self.started_at = None


class Worker:
    def __init__(self, name, conn, addr):
        self.name = name
        self.conn = conn
        self.addr = addr
        self.free = True
        self.inflight = None
        self.last_seen = time.time()
        self.alive = True
        self.mode = "?"
        self.cpu = None
        self.mem_mb = None
        # telemetry refreshed by every heartbeat (see worker.telemetry())
        self.temp_c = None
        self.uptime_s = None
        self.llama_ok = None
        self.models = []
        self.mem_avail_mb = None
        self.current_model = None
        self.last_model_result = None


class ClientSession:
    def __init__(self, conn):
        self.conn = conn
        self.results = []
        self.outstanding = 0
        self.cond = threading.Condition()
        self.send_lock = threading.Lock()

    def deliver(self, payload):
        with self.cond:
            self.results.append(payload)
            self.outstanding -= 1
            self.cond.notify_all()
        try:
            with self.send_lock:
                send_json(self.conn, payload)
        except OSError:
            pass  # client went away; keep the bookkeeping going

    def wait_done(self, timeout):
        deadline = time.time() + timeout
        with self.cond:
            while self.outstanding > 0:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self.cond.wait(remaining)
            return True


class Master:
    def __init__(self):
        self.lock = threading.Lock()
        self.workers = {}  # name -> Worker
        self.pending = queue.Queue()
        self.events = collections.deque(maxlen=100)

    def event(self, kind, text):
        self.events.append({"t": time.strftime("%H:%M:%S"),
                            "kind": kind, "text": text})

    # ---- worker lifecycle --------------------------------------------

    def register(self, name, conn, addr):
        with self.lock:
            if name in self.workers:
                name = f"{name}@{addr[1]}"
            w = Worker(name, conn, addr)
            w.mode = "registering"
            self.workers[name] = w
        self.event("worker", f"worker registered: {name} ({addr[0]})")
        log(f"worker registered: {name} ({addr[0]})")
        threading.Thread(target=self.handle_worker, args=(w,), daemon=True).start()

    def handle_worker(self, w):
        try:
            while True:
                msg = recv_json(w.conn)
                kind = msg.get("type")
                if kind == "MODEL_RESULT":
                    with self.lock:
                        w.last_model_result = msg
                    self.event("model",
                               f"{w.name}: model switch -> {msg.get('model')} "
                               f"({'ok' if msg.get('ok') else 'FAILED: ' + str(msg.get('error'))})")
                    log(f"model result from {w.name}: {msg}")
                    continue
                if kind != "JOB_RESULT":
                    continue
                job = w.inflight
                w.inflight = None
                with self.lock:
                    w.free = True
                if job is None:
                    continue
                if msg.get("status") != "ok":
                    # inference failure counts against MAX_ATTEMPTS too
                    log(f"job {job.id} failed on {w.name}: {msg.get('error')}")
                    self.event("job", f"job {job.id} failed on {w.name}")
                    self.requeue(job)
                    continue
                msg["job_id"] = job.id
                msg["worker"] = w.name
                msg["wall_ms"] = round((time.time() - job.started_at) * 1000, 1)
                log(f"job {job.id} done on {w.name} "
                    f"({msg.get('predicted_per_second')} tok/s)")
                self.event("job", f"job {job.id} done on {w.name} "
                           f"({msg.get('predicted_per_second')} tok/s)")
                job.client.deliver(msg)
        except (ConnectionError, OSError):
            self.mark_dead(w.name)

    def mark_dead(self, name):
        with self.lock:
            w = self.workers.get(name)
            if w is None or not w.alive:
                return
            w.alive = False
            inflight = w.inflight
        try:
            w.conn.close()
        except OSError:
            pass
        with self.lock:
            self.workers.pop(name, None)
        if inflight is not None:
            self.requeue(inflight)
        log(f"worker dead: {name}"
            + (f"; job {inflight.id} requeued" if inflight else ""))
        self.event("worker", f"worker dead: {name}"
                   + (f"; job {inflight.id} requeued" if inflight else ""))

    def requeue(self, job):
        job.attempts += 1
        if job.attempts >= MAX_ATTEMPTS:
            log(f"job {job.id} failed after {MAX_ATTEMPTS} attempts")
            job.client.deliver({"status": "error",
                                "error": f"failed after {MAX_ATTEMPTS} attempts",
                                "job_id": job.id})
            return
        log(f"requeueing job {job.id} (attempt {job.attempts + 1})")
        self.pending.put(job)

    # ---- dispatch ------------------------------------------------------

    def dispatcher(self):
        while True:
            job = self.pending.get()
            w = self.acquire_worker()
            with self.lock:
                w.free = False
                w.inflight = job
            job.started_at = time.time()
            msg = {"type": "JOB", "job_id": job.id, "prompt": job.prompt,
                   "max_tokens": job.max_tokens, "temperature": job.temperature,
                   "seed": job.seed}
            try:
                send_json(w.conn, msg)
                log(f"job {job.id} -> {w.name}")
                self.event("job", f"job {job.id} -> {w.name}")
            except OSError:
                self.mark_dead(w.name)

    def acquire_worker(self):
        while True:
            with self.lock:
                for w in self.workers.values():
                    if w.alive and w.free:
                        return w
            time.sleep(IDLE_POLL)

    # ---- heartbeat + failure monitor ------------------------------------

    def heartbeat_server(self, host):
        srv = listen(host, HEARTBEAT_PORT)
        while True:
            conn, _addr = srv.accept()
            threading.Thread(target=self.handle_heartbeat, args=(conn,),
                             daemon=True).start()

    def handle_heartbeat(self, conn):
        try:
            while True:
                msg = recv_json(conn)
                with self.lock:
                    w = self.workers.get(msg.get("name"))
                    if w is not None:
                        w.last_seen = time.time()
                        for key in ("temp_c", "uptime_s", "llama_ok",
                                    "models", "current_model",
                                    "mem_avail_mb"):
                            if key in msg:
                                setattr(w, key, msg[key])
        except (ConnectionError, OSError):
            pass  # the control handler and monitor decide death

    def monitor(self):
        while True:
            time.sleep(2.0)
            now = time.time()
            with self.lock:
                dead = [w.name for w in self.workers.values()
                        if now - w.last_seen > HEARTBEAT_TIMEOUT]
            for name in dead:
                self.mark_dead(name)

    # ---- clients ---------------------------------------------------------

    def server(self, host):
        srv = listen(host, MASTER_PORT)
        log(f"listening on {host}:{MASTER_PORT} (workers/clients) "
            f"and {host}:{HEARTBEAT_PORT} (heartbeats)")
        while True:
            conn, addr = srv.accept()
            try:
                first = recv_json(conn)
            except (ConnectionError, OSError):
                conn.close()
                continue
            kind = first.get("type")
            if kind == "REGISTER":
                self.register(first.get("name") or "worker", conn, addr)
            elif kind == "SUBMIT":
                threading.Thread(target=self.handle_client,
                                 args=(conn, first), daemon=True).start()
            else:
                conn.close()

    def handle_client(self, conn, submit):
        n = max(1, int(submit.get("count", 1)))
        session = ClientSession(conn)
        conn.settimeout(30)  # never block a worker thread on a stalled client
        session.outstanding = n
        t0 = time.time()
        for _ in range(n):
            job = Job(submit.get("prompt", "Hello"),
                      int(submit.get("max_tokens", 128)),
                      float(submit.get("temperature", 0.0)),
                      int(submit.get("seed", 42)))
            job.client = session
            self.pending.put(job)
        ok = session.wait_done(timeout=max(600, n * 300))
        wall = time.time() - t0
        tokens = sum(r.get("tokens_predicted") or 0 for r in session.results
                     if r.get("status") == "ok")
        done = {"type": "DONE", "jobs": session.results,
                "wall_s": round(wall, 2),
                "aggregate_tok_s": round(tokens / wall, 2) if wall and tokens else None,
                "timed_out": not ok}
        try:
            with session.send_lock:
                send_json(conn, done)
        except OSError:
            pass
        conn.close()
        log(f"client done: {n} job(s), wall {wall:.2f}s, "
            f"aggregate {done['aggregate_tok_s']} tok/s")
        self.event("client", f"client done: {n} job(s), {wall:.2f}s, "
                   f"{done['aggregate_tok_s']} tok/s")

    # ---- management API (read-only status + model switching) -----------

    def snapshot(self):
        with self.lock:
            workers = []
            for w in self.workers.values():
                workers.append({
                    "name": w.name, "addr": w.addr[0], "alive": w.alive,
                    "free": w.free, "inflight": (w.inflight.id
                                                 if w.inflight else None),
                    "mode": w.mode, "cpu": w.cpu, "mem_mb": w.mem_mb,
                    "temp_c": w.temp_c, "uptime_s": w.uptime_s,
                    "llama_ok": w.llama_ok, "models": list(w.models or []),
                    "mem_avail_mb": w.mem_avail_mb,
                    "current_model": w.current_model,
                    "last_seen": w.last_seen,
                    "last_model_result": w.last_model_result,
                })
            return {"workers": workers,
                    "pending": self.pending.qsize(),
                    "events": list(self.events)}

    def broadcast_set_model(self, name):
        """Ask every alive worker to switch its llama-server to `model`."""
        if not MODEL_NAME_RE.match(name or ""):
            return {"sent": [], "error": f"invalid model name: {name!r}"}
        sent = []
        with self.lock:
            for w in self.workers.values():
                if not w.alive:
                    continue
                try:
                    send_json(w.conn, {"type": "SET_MODEL", "model": name})
                    sent.append(w.name)
                except OSError:
                    pass
        if sent:
            self.event("model", f"model switch requested: {name} -> {sent}")
        log(f"SET_MODEL {name} sent to {sent}")
        return {"sent": sent}


class StatusAPI:
    """Tiny read-only HTTP API on the master (stdlib http.server)."""

    def __init__(self, master):
        self.master = master

    def serve(self, host, port=STATUS_PORT):
        master = self.master

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _json(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/cluster":
                    self._json(200, master.snapshot())
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                if self.path != "/api/model":
                    self._json(404, {"error": "not found"})
                    return
                try:
                    length = min(int(self.headers.get("Content-Length", 0)),
                                 4096)
                    payload = json.loads(self.rfile.read(length).decode())
                    name = str(payload.get("model", ""))
                except (ValueError, OSError):
                    self._json(400, {"error": "bad request"})
                    return
                self._json(200, master.broadcast_set_model(name))

        server = ThreadingHTTPServer((host, port), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="cluster master")
    ap.add_argument("--bind", default="0.0.0.0",
                    help="address to listen on; workers/clients must be able "
                         "to reach it (default: all interfaces — run only on "
                         "a trusted LAN)")
    args = ap.parse_args()

    m = Master()
    threading.Thread(target=m.dispatcher, daemon=True).start()
    threading.Thread(target=m.heartbeat_server, args=(args.bind,),
                     daemon=True).start()
    threading.Thread(target=m.monitor, daemon=True).start()
    StatusAPI(m).serve(args.bind, STATUS_PORT)
    try:
        m.server(args.bind)
    except KeyboardInterrupt:
        pass
