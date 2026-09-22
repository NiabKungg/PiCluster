#!/usr/bin/env python3
"""Generate report figures from results/scenario_*.txt files.

Run from the repository root:  python3 plot_results.py
Uses only literal relative paths (results/...).
"""

import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

for font in ("/usr/share/fonts/truetype/tlwg/Loma.ttf",
             "/usr/share/fonts/truetype/tlwg/Garuda.ttf"):
    if os.path.isfile(font):
        font_manager.fontManager.addfont(font)
plt.rcParams["font.family"] = "Loma"
plt.rcParams["axes.unicode_minus"] = False

JOB_RE = re.compile(r"job\s+(\d+)\s+on\s+(\S+)\s+([\d.]+)\s+tok/s\s+\(([\d.]+)\s*ms\)")
WALL_RE = re.compile(r"wall time\s*:\s*([\d.]+)s")
AGG_RE = re.compile(r"aggregate\s*:\s*([\d.]+)\s*tok/s")


def parse(fname):
    jobs, wall, agg = [], None, None
    with open(fname) as f:
        for ln in f:
            m = JOB_RE.search(ln)
            if m:
                jobs.append({"job": int(m.group(1)), "worker": m.group(2),
                             "tok_s": float(m.group(3)),
                             "ms": float(m.group(4))})
            w = WALL_RE.search(ln)
            if w:
                wall = float(w.group(1))
            a = AGG_RE.search(ln)
            if a:
                agg = float(a.group(1))
    return {"jobs": jobs, "wall": wall, "agg": agg}


def main():
    data = {}
    if os.path.isfile("results/scenario_A.txt"):
        data["A"] = parse("results/scenario_A.txt")
    if os.path.isfile("results/scenario_B.txt"):
        data["B"] = parse("results/scenario_B.txt")
    if os.path.isfile("results/scenario_C.txt"):
        data["C"] = parse("results/scenario_C.txt")

    os.makedirs("results/figs", exist_ok=True)
    labels = {"A": "1 worker\n(เครื่องเดียว)", "C": "2 workers\n(request-parallel)"}
    names = [n for n in ("A", "C") if n in data]
    colors = ["#4c72b0", "#55a868"]

    # ---- fig 1: aggregate throughput ------------------------------------
    fig, ax = plt.subplots(figsize=(7, 4.5))
    vals = [data[n]["agg"] for n in names]
    bars = ax.bar([labels[n] for n in names], vals,
                  color=colors[:len(names)], width=0.5)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.2, f"{v:.2f}",
                ha="center", fontsize=12, fontweight="bold")
    if "A" in data:
        ax.axhline(data["A"]["agg"] * 2, ls="--", color="#c44e52",
                   label=f"อุดมคติ 2 เท่า = {data['A']['agg'] * 2:.2f}")
        ax.legend()
    ax.set_ylabel("Aggregate throughput (tok/s)")
    ax.set_title("Throughput รวมของคลัสเตอร์: 1 worker vs 2 workers\n"
                 "(Qwen2.5-Coder-0.5B, 8 jobs, 128 tokens/job)")
    fig.tight_layout()
    fig.savefig("results/figs/fig1_aggregate.png", dpi=150)
    plt.close(fig)

    # ---- fig 2: per-job tok/s --------------------------------------------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    width = 0.35
    for i, n in enumerate(names):
        jobs = sorted(data[n]["jobs"], key=lambda j: j["job"])
        xs = range(len(jobs))
        ax.bar([x + i * width - width / 2 for x in xs],
               [j["tok_s"] for j in jobs], width,
               label=f"scenario {n}: {len(jobs)} jobs", alpha=0.9)
    ax.set_xlabel("หมายเลข job")
    ax.set_ylabel("tok/s ต่อ job")
    ax.set_title("tok/s ราย job — ทุก job ยังได้ความเร็วเต็มของเครื่องตัวเอง\n"
                 "การกระจายงานไม่ลดคุณภาพของแต่ละ request")
    ax.legend()
    fig.tight_layout()
    fig.savefig("results/figs/fig2_per_job.png", dpi=150)
    plt.close(fig)

    # ---- fig 3: wall time --------------------------------------------------
    fig, ax = plt.subplots(figsize=(7, 4.5))
    walls = [data[n]["wall"] for n in names]
    bars = ax.bar([labels[n] for n in names], walls,
                  color=colors[:len(names)], width=0.5)
    for b, v in zip(bars, walls):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.1f}s",
                ha="center", fontsize=12, fontweight="bold")
    ax.set_ylabel("Wall time รวม (วินาที)")
    ax.set_title("เวลารวมในการ serve งาน 8 jobs —\n"
                 "คลัสเตอร์ลดเวลารอรวมของผู้ใช้ทุกคนลง ~เท่าตัว")
    fig.tight_layout()
    fig.savefig("results/figs/fig3_walltime.png", dpi=150)
    plt.close(fig)

    # ---- fig 4: scenario B (model split) — เมื่อมีข้อมูล -----------------
    if "B" in data and data["B"]["jobs"]:
        base = data["A"]["jobs"][0]["tok_s"]
        split = [j["tok_s"] for j in data["B"]["jobs"]]
        mean_split = sum(split) / len(split)
        fig, ax = plt.subplots(figsize=(7, 4.5))
        bars = ax.bar(["เครื่องเดียว\n(โมเดลอยู่ใน RAM เดียว)",
                       "แบ่งโมเดลข้ามเครื่อง\n(RPC split)"],
                      [base, mean_split],
                      color=["#55a868", "#c44e52"], width=0.5)
        for b, v in zip(bars, [base, mean_split]):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.15, f"{v:.2f}",
                    ha="center", fontsize=12, fontweight="bold")
        ax.set_ylabel("tok/s ต่อ request")
        ax.set_title("ทำไม 'แบ่งโมเดลข้ามเครื่อง' ไม่ช่วยให้คำตอบเดียวเร็วขึ้น:\n"
                     "token generation ผูกกับ RAM bandwidth "
                     "และทุก token ต้องข้ามเครือข่าย")
        fig.tight_layout()
        fig.savefig("results/figs/fig4_split.png", dpi=150)
        plt.close(fig)

    print("figures written to results/figs/")
    for n in names:
        print(f"scenario {n}: jobs={len(data[n]['jobs'])} "
              f"wall={data[n]['wall']} agg={data[n]['agg']}")


if __name__ == "__main__":
    main()
