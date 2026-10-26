# 🍓 PiCluster — คลัสเตอร์ Raspberry Pi กระจาย user ที่มาพร้อมกัน ให้ Local LLM

โปรเจควิชาระบบปฏิบัติการ (Operating Systems): คลัสเตอร์ Raspberry Pi พร้อมระบบ
กระจายงานแบบ **master–worker ที่เขียนเองทั้งหมด** (Python, ไม่ใช้ framework)
ใช้ **local LLM (llama.cpp)** เป็น workload จริง

**ปัญหาที่แก้**: ถ้ามี Pi เครื่องเดียวแล้ว user เข้ามาพร้อมกัน 2 คน คนที่สองต้อง
รอคิวให้ LLM รัน prompt ของคนแรกเสร็จก่อน — แต่คลัสเตอร์นี้กระจายงานไปทุกเครื่อง
**user ไม่ต้องรอคิวกันเอง**

## 📊 ผลจริงล่าสุด (วัดวันที่ระบบรวม Pi 5)

ยิง 4 requests พร้อมกันเข้าหน้าเว็บ:

| worker | งานที่ได้ | ความเร็ว |
|---|---|---|
| rpi5b-1 (Pi 5) | 3 งานพร้อมกัน | **21–23 tok/s ต่องาน** |
| rpi4b-2 (Pi 4) | 1 งาน | 8.5 tok/s |

- ผลรวม ~70 tok/s เมื่อ user ยิงพร้อมกัน — แต่ละคนยังได้ความเร็วเต็ม ๆ
  ของเครื่องที่ตัวเองโดนกระจายไป (เทียบ: เครื่องเดียวทั้ง 4 คนต้องต่อแถวรอ)
- benchmark ฉบับเต็ม (2× Pi 4, scenario A/B/C + กราฟ) ดูได้ใน git history

## 🏗️ สถาปัตยกรรม

```
browser ──▶ webui :8000 ──SUBMIT──▶ master :5555 ──JOB──▶ worker rpi4b-2 ──▶ llama-server
                                     │    ◀─JOB_RESULT─ (loopback)
                                     └──JOB──▶ worker rpi5b-1  ──▶ llama-server
                                              ◀─JOB_RESULT─ (loopback)
        heartbeat ทุก 2 วิ (พอร์ต 5556) จากทุก worker ▲
```

- **Protocol**: 4-byte length prefix + JSON ผ่าน TCP (`common.py`)
- **Scheduling**: first-free-worker — ใครว่างได้งานไปก่อน
- **Fault tolerance**: ไม่ได้รับ heartbeat เกิน 8 วิ → ถือว่าตาย, job ค้าง
  **หรือ job ที่รายงาน error** ถูกส่งใหม่ให้ worker อื่นอัตโนมัติ (at-least-once)
- **Self-heal**: worker ส่ง REGISTER ซ้ำทุก 20 วิ — ถ้า master พลาดตัด worker
  ออก (เช่น heartbeat มาช่วง boot) มันจะโผล่กลับมาเองใน ≤20 วิ โดยไม่หลุด connection

## 📡 LAN → WiFi failover

worker รับรายชื่อ master ได้หลายที่อยู่คั่นด้วยจุด (`--master ip1,ip2`) —
หลุดทางไหนลองทางถัดไปอัตโนมัติ ทำให้ node ที่มีทั้งสายแลนและ WiFi ยังให้บริการต่อ
แม้สายแลนถูกถอด: route เปลี่ยนไปวิ่ง wlan0, control connection ตาย,
worker reconnect แล้ว register ใหม่ผ่าน WiFi ภายใน ~10 วินาที
(ทดสอบจริง: `ip link set eth0 down` → master เห็น worker เปลี่ยน IP จาก
.124 → .125 พร้อมให้บริการต่อทันที)

## 🖥️ ฮาร์ดแวร์และซอฟต์แวร์

| Node | บทบาท | เด่น |
|---|---|---|
| rpi4b-2 (Pi 4, RAM 2GB) | master + webui + worker | PSU 5.1V/3A |
| rpi5b-1 (Pi 5, RAM 8GB) | worker | จ่ายไฟผ่าน 24V/5A + buck XL4015 (5.1V) |

- OS: Raspberry Pi OS (64-bit) — rpi4b-2 ใช้ Bookworm, rpi5b-1 ใช้ Trixie
- Inference: llama.cpp (llama-server ต่อ node), โมเดล Qwen2.5-Coder-0.5B-Instruct Q4_K_M (491 MB)
- เครือข่าย: LAN ผ่าน router (192.168.1.0/24) + WiFi `Niab_2.4G` เป็นทางสำรอง

## 🚀 การใช้งาน

### เตรียมแต่ละ node

```bash
sudo apt install -y git cmake build-essential
git clone --depth 1 https://github.com/ggml-org/llama.cpp ~/llama.cpp
cmake -S ~/llama.cpp -B ~/llama.cpp/build && cmake --build ~/llama.cpp/build -j4
mkdir -p ~/models && cd ~/models
wget https://huggingface.co/Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-0.5b-instruct-q4_k_m.gguf
git clone https://github.com/NiabKungg/PiCluster.git ~/pi-cluster
```

ทดสอบ pipeline โดยไม่ต้องมี llama.cpp: `python3 worker.py --master <ip> --name w1 --mock`

### รันคลัสเตอร์ (มี systemd แล้วไม่ต้องสั่งมือ — ด้านล่างคือแบบ manual)

```bash
# บน rpi4b-2 (master + webui + worker ของตัวเอง)
cd ~/pi-cluster
python3 master.py
python3 webui.py            # http://192.168.1.222:8000
python3 worker.py --master 127.0.0.1 --name rpi4b-2 --model qwen2.5-coder-0.5b-instruct-q4_k_m.gguf

# บน rpi5b-1 (worker — ระบุ master หลายที่อยู่ได้สำหรับ failover)
python3 worker.py --master 192.168.1.222 --name rpi5b-1 --model qwen2.5-coder-0.5b-instruct-q4_k_m.gguf

# ยิงงานทดสอบ N requests พร้อมกัน (จำลอง user หลายคน)
python3 client.py --master 192.168.1.222 --requests 4 --max-tokens 128
```

### Web Console (แชท + สถานะ + สลับโมเดล)

เปิด `http://192.168.1.222:8000`:

- **แชทผ่านคลัสเตอร์**: ทุกข้อความเข้า master แล้วกระจายให้ worker ที่ว่าง —
  bubble คำตอบบอกเสมอว่า **Pi ตัวไหนประมวลผล, กี่ tok/s, กี่วินาที**
- **สถานะโหนด**: อุณหภูมิ / RAM / uptime / สุขภาพ llama-server / โมเดลปัจจุบัน
  ของทุกเครื่อง — มาจาก heartbeat ของ worker เอง (โปรโตคอลเดียวกัน ไม่ใช้ SSH)
- **สลับโมเดล**: เลือก .gguf ที่มีใน `~/models` แล้ว master สั่งผ่าน control channel
- master เปิด status API ที่พอร์ต 5557 (`GET /api/cluster`, `POST /api/model`)

### Auto-start หลังรีบูต (systemd)

```bash
# บน rpi4b-2 (master)
sudo cp ~/pi-cluster/systemd/*.service /etc/systemd/system/
sudo sed -i 's/rpi4b-1/rpi4b-2/g' /etc/systemd/system/picluster-master.service \
                                /etc/systemd/system/picluster-webui.service
sudo sed -i 's/--master [^ ]*/--master 127.0.0.1/' /etc/systemd/system/picluster-worker@.service
sudo systemctl daemon-reload
sudo systemctl enable --now picluster-master picluster-webui picluster-worker@rpi4b-2

# บน rpi5b-1 (worker)
sudo cp ~/pi-cluster/systemd/picluster-worker@.service /etc/systemd/system/
sudo sed -i 's/--master [^ ]*/--master 192.168.1.222/' /etc/systemd/system/picluster-worker@.service
sudo systemctl daemon-reload
sudo systemctl enable --now picluster-worker@rpi5b-1
```

worker ยังเปิด TCP keepalive + ตรวจ heartbeat failure กัน connection แบบ
half-open หลัง link flap — หลุดเมื่อไหร่ reconnect + register ใหม่เองเสมอ

## 🎓 แนวคิด OS ที่ครอบคลุม

- **Process & IPC**: job dispatch ผ่าน TCP socket, JSON framing เองทั้งหมด
- **Concurrency**: threading บน master (dispatcher, heartbeat monitor,
  connection handlers) พร้อม lock; worker มี heartbeat thread + announce thread
- **Fault tolerance**: heartbeat + timeout + job requeue + worker failover
  (ลอง Ctrl-C worker กลางงาน — job ไม่หาย, ถอดสายแลน node — ระบบยังหายใจ)
- **Scheduling**: first-free-worker dispatch, load balancing
- **Memory hierarchy**: ทำไม token generation ผูกกับ RAM bandwidth
  และ network จึงเป็น bottleneck ของการ split โมเดล
- **Page cache**: รอบวัดแรกช้ากว่ารอบถัดไป — จึงต้อง warm-up ก่อนวัด

## 🧯 ปัญหาที่พบจริงระหว่างทำ (บทเรียนสำคัญ)

1. **build llama.cpp ตายกลางทางบน RAM 2GB** → เพิ่ม swap 2GB + build ด้วย `-j2`
2. **PSU อ่อนทำให้เครื่องดับเองตอนโหลดหนัก** → `journalctl -k | grep -i undervolt`
   จับได้; Pi 5 ต้องการ 5V/5A — ชั่วคราวใช้ 24V/5A adapter + buck XL4015
   ตั้ง 5.1V (undervoltage counter = 0 ตลอดการโหลด)
3. **ไฟดับกลางการเขียนทำ SD card เสียหายจน boot ไม่ขึ้น** → เขียน cloud-init
   `user-data` ให้ตั้งค่าเองตอน boot ทุกครั้ง (bootcmd = PER_BOOT) ทำให้ re-flash
   แล้วกู้ระบบได้ในไม่กี่นาที; fsck.repair=yes ช่วยกู้ rootfs ที่เสียบางส่วน
4. **worker กลายเป็น "ผี"** (master ตัดออกจากตารางแต่ connection ยังอยู่
   worker ไม่ยอม register ใหม่) → worker ส่ง REGISTER ซ้ำทุก 20 วิ,
   master re-add จาก refresh นั้น (commit `d5f9fea`)
5. **worker ตายเป๊ะทุก 5 วินาที** → socket จาก `create_connection(timeout=5)`
   ไม่ได้เคลียร์ timeout ก่อนเข้าลูปรับ job — ต้อง `settimeout(None)`
6. **รีบูตติดกันเร็ว ๆ แล้ว node ที่บูตจาก USB หายจากเครือข่าย** → เว้นปลั๊ก
   ≥30 วิ + `PROGRAM_USB_BOOT_TIMEOUT=1` ใน EEPROM
7. **สาย LAN ขาดคู่สาย** → link ได้แค่ 100M (gigabit ต้องครบ 4 คู่)

## 📁 โครงสร้าง

```
pi-cluster/
├── common.py        # JSON framing ผ่าน TCP (+ send lock กัน thread ชนกัน)
├── master.py        # scheduler: dispatch, heartbeat monitor, requeue, status API
├── worker.py        # agent: spawn llama-server, รับ job, heartbeat, failover, self-heal
├── webui.py         # web console (แชท/สถานะ/สลับโมเดล) — stdlib ล้วน
├── ui.html          # หน้าบ้านของ web console
├── client.py        # ยิง N requests พร้อมกันจำลอง user หลายคน + สรุปผล
├── build-llama.sh   # สคริปต์ build llama.cpp
└── systemd/         # unit files: master, webui, worker@.service (template)
```
