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

import itertools
import queue
import socket
import threading
import time

from common import MASTER_PORT, HEARTBEAT_PORT, recv_json, send_json

HEARTBEAT_TIMEOUT = 8.0  # seconds without a heartbeat -> worker assumed dead
MAX_ATTEMPTS = 3         # a job is executed at most this many times
IDLE_POLL = 0.1


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

    # ---- worker lifecycle --------------------------------------------

    def register(self, name, conn, addr):
        with self.lock:
            if name in self.workers:
                name = f"{name}@{addr[1]}"
            w = Worker(name, conn, addr)
            self.workers[name] = w
        log(f"worker registered: {name} ({addr[0]})")
        threading.Thread(target=self.handle_worker, args=(w,), daemon=True).start()

    def handle_worker(self, w):
        try:
            while True:
                msg = recv_json(w.conn)
                if msg.get("type") != "JOB_RESULT":
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
                    self.requeue(job)
                    continue
                msg["job_id"] = job.id
                msg["worker"] = w.name
                msg["wall_ms"] = round((time.time() - job.started_at) * 1000, 1)
                log(f"job {job.id} done on {w.name} "
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
    try:
        m.server(args.bind)
    except KeyboardInterrupt:
        pass
