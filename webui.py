#!/usr/bin/env python3
"""Web console for the Pi cluster — serves the chat UI on :8000.

Runs on the same Pi as the master. All upstream traffic goes to fixed
loopback endpoints only: the master's TCP protocol (chat) and its
status API on :5557 (status, model switching). No subprocess, no SSH.
"""

import http.client
import json
import os
import socket
import sys

BASE = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import common  # noqa: E402
from common import MASTER_PORT, recv_json, send_json  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

STATUS_PORT = 5557
HTTP_PORT = 8000
HTML_PATH = os.path.join(BASE, "ui.html")


def fetch_status():
    conn = http.client.HTTPConnection("127.0.0.1", STATUS_PORT, timeout=6)
    try:
        conn.request("GET", "/api/cluster")
        resp = conn.getresponse()
        return json.loads(resp.read().decode())
    finally:
        conn.close()


def request_model_switch(name):
    conn = http.client.HTTPConnection("127.0.0.1", STATUS_PORT, timeout=10)
    try:
        body = json.dumps({"model": name})
        conn.request("POST", "/api/model", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return json.loads(resp.read().decode())
    finally:
        conn.close()


def cluster_chat(prompt, max_tokens, temperature, seed, timeout_s=300):
    """Submit one job through the cluster and wait for its result."""
    conn = socket.create_connection(("127.0.0.1", MASTER_PORT), timeout=10)
    conn.settimeout(timeout_s)
    try:
        send_json(conn, {"type": "SUBMIT", "count": 1, "prompt": prompt,
                         "max_tokens": max_tokens,
                         "temperature": temperature, "seed": seed})
        while True:
            msg = recv_json(conn)
            if msg.get("type") == "JOB_RESULT":
                return msg
            if msg.get("type") == "DONE":
                return {"status": "error", "error": "no worker result"}
    finally:
        conn.close()


def common_models(status):
    lists = [set(w.get("models") or []) for w in status.get("workers", [])
             if w.get("alive")]
    if not lists:
        return []
    inter = set.intersection(*lists) if lists else set()
    return sorted(inter)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            try:
                with open(HTML_PATH, "rb") as f:
                    body = f.read()
            except OSError:
                self._json(500, {"error": "ui.html missing"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/status":
            try:
                self._json(200, fetch_status())
            except (OSError, ValueError) as exc:
                self._json(503, {"error": f"master unreachable: {exc}"})
        elif path == "/api/models":
            try:
                status = fetch_status()
                self._json(200, {"models": common_models(status),
                                 "status": status})
            except (OSError, ValueError) as exc:
                self._json(503, {"error": f"master unreachable: {exc}"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 65536)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, OSError):
            self._json(400, {"error": "bad request"})
            return
        if path == "/api/chat":
            prompt = payload.get("prompt")
            if (not isinstance(prompt, str) or not prompt.strip()
                    or len(prompt) > 2000):
                self._json(400, {"error": "prompt must be 1-2000 chars"})
                return
            try:
                max_tokens = min(max(int(payload.get("max_tokens", 128)),
                                     1), 512)
                temperature = min(max(float(payload.get("temperature", 0.7)),
                                      0.0), 2.0)
                seed = int(payload.get("seed", 42))
            except (TypeError, ValueError):
                self._json(400, {"error": "bad generation params"})
                return
            try:
                result = cluster_chat(prompt.strip(), max_tokens,
                                      temperature, seed)
                self._json(200, result)
            except (OSError, ValueError) as exc:
                self._json(503, {"error": f"cluster unreachable: {exc}"})
        elif path == "/api/model":
            name = payload.get("model")
            if not isinstance(name, str) or len(name) > 128:
                self._json(400, {"error": "bad model name"})
                return
            try:
                self._json(200, request_model_switch(name))
            except (OSError, ValueError) as exc:
                self._json(503, {"error": f"master unreachable: {exc}"})
        else:
            self._json(404, {"error": "not found"})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    server.daemon_threads = True
    print(f"[webui] serving on 0.0.0.0:{HTTP_PORT} (ui: {HTML_PATH})",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
