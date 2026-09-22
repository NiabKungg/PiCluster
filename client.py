#!/usr/bin/env python3
"""Client: submits N inference jobs to the master and prints a summary.

Example:
  python3 client.py --master 192.168.1.10 --requests 8 --max-tokens 128
"""

import argparse
import socket
import time

from common import MASTER_PORT, recv_json, send_json


def main():
    ap = argparse.ArgumentParser(description="cluster client")
    ap.add_argument("--master", required=True, help="master IP or hostname")
    ap.add_argument("--requests", type=int, default=1)
    ap.add_argument("--prompt",
                    default="Explain what an operating system does, in two sentences.")
    ap.add_argument("--max-tokens", type=int, default=128)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    conn = socket.create_connection((args.master, MASTER_PORT), timeout=10)
    conn.settimeout(None)  # results can take minutes; block until they arrive
    send_json(conn, {"type": "SUBMIT", "count": args.requests,
                     "prompt": args.prompt, "max_tokens": args.max_tokens,
                     "temperature": args.temperature, "seed": args.seed})
    print(f"submitted {args.requests} job(s); waiting...\n")

    t0 = time.time()
    done = None
    try:
        while done is None:
            msg = recv_json(conn)
            kind = msg.get("type")
            if kind == "JOB_RESULT":
                if msg.get("status") == "ok":
                    print(f"  job {msg['job_id']:>3} on {msg['worker']:<14}"
                          f"{msg.get('predicted_per_second')} tok/s"
                          f"  ({msg.get('wall_ms')} ms)")
                else:
                    print(f"  job {msg.get('job_id')} FAILED: {msg.get('error')}")
            elif kind == "DONE":
                done = msg
    except (ConnectionError, KeyboardInterrupt) as exc:
        print(f"\naborted: {exc}")
        return

    wall = done["wall_s"]
    jobs = done["jobs"]
    ok_jobs = [j for j in jobs if j.get("status") == "ok"]
    speeds = [j["predicted_per_second"] for j in ok_jobs
              if j.get("predicted_per_second")]
    print("\n==== summary ====")
    print(f"jobs ok/total : {len(ok_jobs)}/{len(jobs)}")
    print(f"wall time     : {wall}s")
    if speeds:
        print(f"tok/s per job : mean {sum(speeds) / len(speeds):.2f}, "
              f"min {min(speeds):.2f}, max {max(speeds):.2f}")
    print(f"aggregate     : {done.get('aggregate_tok_s')} tok/s "
          f"(total tokens generated / wall time)")
    if done.get("timed_out"):
        print("WARNING: master timed out waiting for some jobs")


if __name__ == "__main__":
    main()
