#!/usr/bin/env python3
"""Telegram status bot for the Pi cluster.

Reads TG_BOT_TOKEN from the environment only (never stored in this file).
Chat registrations are kept in memory: after a restart, send /start once
to re-register.

Watches each Pi with a TCP connect to its SSH port and messages every
registered chat when a Pi goes online or offline. The WiFi name is a
display label per target because the watcher runs outside the Pis.

Commands: /start  /status  /help
"""

import argparse
import http.client
import ipaddress
import json
import os
import re
import socket
import time

API_HOST = "api.telegram.org"
API_PORT = 443
SAFE_TOKEN = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def log(msg):
    print(f"[tgbot {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def validate_api_host():
    """Only talk to the real Telegram API: https, exact host, and the
    name must not resolve into a private/loopback/link-local range."""
    infos = socket.getaddrinfo(API_HOST, API_PORT, proto=socket.IPPROTO_TCP)
    if not infos:
        raise SystemExit(f"cannot resolve {API_HOST}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            raise SystemExit(f"{API_HOST} resolved to forbidden {ip}")


def telegram_call(token, method, payload=None, timeout=15):
    """POST JSON to https://api.telegram.org — host is a fixed literal."""
    if not SAFE_TOKEN.match(token):
        raise SystemExit("TG_BOT_TOKEN does not look like a bot token")
    conn = http.client.HTTPSConnection(API_HOST, API_PORT, timeout=timeout)
    try:
        conn.request("POST", "/bot" + token + "/" + method,
                     body=json.dumps(payload or {}),
                     headers={"Content-Type": "application/json"})
        data = json.loads(conn.getresponse().read().decode())
    finally:
        conn.close()
    if not data.get("ok"):
        raise RuntimeError(f"telegram {method}: {data}")
    return data["result"]


def ssh_reachable(ip, timeout=2.0):
    try:
        with socket.create_connection((ip, 22), timeout=timeout):
            return True
    except OSError:
        return False


def parse_target(spec):
    """name=ip[:wifi-label]"""
    name, _, rest = spec.partition("=")
    ip, _, wifi = rest.partition(":")
    if not (name and ip):
        raise SystemExit(f"--pi must be name=ip[:wifi], got: {spec}")
    if not SAFE_NAME.match(name):
        raise SystemExit(f"unsafe pi name: {name}")
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise SystemExit(f"invalid ip: {ip}")
    return name, ip, (wifi or "")


def online_message(name, ip, wifi):
    bits = [f"SSH port 22 @ {ip}"]
    if wifi:
        bits.append(f"WiFi: {wifi}")
    return f"🟢 {name} online — " + ", ".join(bits)


class PiMonitor:
    def __init__(self, targets):
        self.targets = targets  # list of (name, ip, wifi)
        self.state = {t[0]: {"online": None, "fails": 0,
                             "last_seen": None} for t in targets}

    def probe_all(self, notify):
        for name, ip, wifi in self.targets:
            st = self.state[name]
            if ssh_reachable(ip):
                st["fails"] = 0
                st["last_seen"] = time.strftime("%H:%M:%S")
                if st["online"] is not True:
                    first = st["online"] is None
                    st["online"] = True
                    if first:
                        log(f"{name} online (first probe, no alert)")
                    else:
                        notify(online_message(name, ip, wifi))
            else:
                st["fails"] += 1
                if st["online"] and st["fails"] >= 2:
                    st["online"] = False
                    notify(f"🔴 {name} offline ไม่ตอบแล้ว "
                           f"(IP {ip}, เห็นครั้งสุดท้าย "
                           f"{st['last_seen'] or '?'})")

    def snapshot(self):
        rows = []
        for name, ip, wifi in self.targets:
            st = self.state[name]
            if st["online"]:
                rows.append(online_message(name, ip, wifi))
            else:
                state_txt = "กำลังรอยืนยัน..." if st["fails"] else "offline"
                rows.append(f"🔴 {name}: {state_txt} (IP {ip})")
        return "\n\n".join(rows)


def main():
    ap = argparse.ArgumentParser(description="Pi status bot for Telegram")
    ap.add_argument("--pi", action="append", default=[],
                    help="name=ip[:wifi-label], repeatable")
    ap.add_argument("--interval", type=int, default=20,
                    help="probe interval seconds")
    args = ap.parse_args()

    token = os.environ.get("TG_BOT_TOKEN")
    if not token:
        raise SystemExit("export TG_BOT_TOKEN=<token from BotFather> first")

    validate_api_host()
    targets = [parse_target(s) for s in (args.pi or [
        "rpi4b-1=192.168.1.223:Niab_2.4G",
        "rpi4b-2=192.168.1.222:Niab_2.4G"])]

    me = telegram_call(token, "getMe")
    log(f"bot @{me['username']} watching: "
        + ", ".join(f"{n}({i})" for n, i, _ in targets))

    monitor = PiMonitor(targets)
    chats = set()
    offset = 0

    def send(chat_id, text):
        telegram_call(token, "sendMessage", {"chat_id": chat_id,
                                             "text": text})

    def broadcast(text):
        for chat_id in list(chats):
            try:
                send(chat_id, text)
            except (OSError, RuntimeError, ValueError) as exc:
                log(f"send to {chat_id} failed: {exc}")

    last_probe = 0.0
    while True:
        try:
            for upd in telegram_call(token, "getUpdates",
                                     {"offset": offset, "timeout": 0},
                                     timeout=10):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                chat_id = (msg.get("chat") or {}).get("id")
                text = (msg.get("text") or "").strip()
                if not (chat_id and text):
                    continue
                if text in ("/start", "/help"):
                    if chat_id not in chats:
                        chats.add(chat_id)
                        log(f"new chat registered: {chat_id}")
                        send(chat_id,
                             "👋 จดจำแชทนี้แล้ว จะแจ้งทันทีเมื่อ Pi "
                             "ออนไลน์/ออฟไลน์\n/status = สถานะตอนนี้\n"
                             "/help = คำสั่งทั้งหมด")
                        broadcast(monitor.snapshot())
                    else:
                        send(chat_id, "แชทนี้จดจำอยู่แล้ว\n"
                             "/status = สถานะตอนนี้")
                elif text == "/status":
                    send(chat_id, monitor.snapshot())
                else:
                    send(chat_id, "ส่ง /status เพื่อดูสถานะ หรือ /help")
        except (OSError, RuntimeError, ValueError) as exc:
            log(f"telegram error: {exc}")

        if time.time() - last_probe >= args.interval:
            last_probe = time.time()
            monitor.probe_all(broadcast)
        time.sleep(3)


if __name__ == "__main__":
    main()
