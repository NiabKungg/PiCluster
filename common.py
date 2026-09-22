"""Shared helpers: JSON framing over TCP.

Every message is 4-byte big-endian length prefix + UTF-8 JSON payload.
"""

import json
import struct

MASTER_PORT = 5555   # worker registration, job dispatch, client requests
HEARTBEAT_PORT = 5556  # worker heartbeats


def send_json(sock, obj):
    data = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return buf


def recv_json(sock):
    (size,) = struct.unpack(">I", recv_exact(sock, 4))
    return json.loads(recv_exact(sock, size).decode("utf-8"))
