#!/usr/bin/env python3
"""สร้างรายงาน PDF ฉบับเต็มของโปรเจคคลัสเตอร์ — ใช้ฟอนต์ไทย Garuda/Loma"""

import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "report", "PiCluster_Report.pdf")
FIGS = os.path.join(BASE, "results", "figs")

FONT_DIR = "/usr/share/fonts/truetype/tlwg"
pdfmetrics.registerFont(TTFont("Garuda", os.path.join(FONT_DIR, "Garuda.ttf")))
BOLD_PATH = os.path.join(FONT_DIR, "Garuda-Bold.ttf")
HAS_BOLD = os.path.isfile(BOLD_PATH)
if HAS_BOLD:
    pdfmetrics.registerFont(TTFont("Garuda-Bold", BOLD_PATH))
else:
    pdfmetrics.registerFont(TTFont("Garuda-Bold", os.path.join(FONT_DIR, "Garuda.ttf")))

S_BODY = ParagraphStyle("body", fontName="Garuda", fontSize=11, leading=17,
                        wordWrap="CJK", spaceAfter=6)
S_H1 = ParagraphStyle("h1", fontName="Garuda-Bold", fontSize=15, leading=20,
                      spaceBefore=14, spaceAfter=8, textColor=colors.HexColor("#1a3c6e"))
S_H2 = ParagraphStyle("h2", fontName="Garuda-Bold", fontSize=12.5, leading=17,
                      spaceBefore=10, spaceAfter=6)
S_TITLE = ParagraphStyle("title", fontName="Garuda-Bold", fontSize=22, leading=30,
                         alignment=1, textColor=colors.HexColor("#1a3c6e"))
S_SUB = ParagraphStyle("sub", fontName="Garuda", fontSize=13, leading=20,
                       alignment=1)
S_CAP = ParagraphStyle("cap", fontName="Garuda", fontSize=9.5, leading=13,
                       alignment=1, textColor=colors.HexColor("#555555"))
S_MONO = ParagraphStyle("mono", fontName="Courier", fontSize=9, leading=12,
                        backColor=colors.HexColor("#f4f4f4"), leftIndent=10,
                        spaceAfter=6)


def H1(t):
    return Paragraph(t, S_H1)


def H2(t):
    return Paragraph(t, S_H2)


def P(t):
    return Paragraph(t, S_BODY)


def B(t):
    return Paragraph("<b>" + t + "</b>", S_BODY)


def IMG(path, caption, width=15.5 * cm):
    img = Image(path)
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth = width
    img.drawHeight = width * ratio
    return [img, Paragraph(caption, S_CAP), Spacer(1, 8)]


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Garuda", 8.5)
    canvas.drawCentredString(A4[0] / 2, 1.1 * cm,
                             f"หน้า {doc.page}  |  PiCluster — รายงานโปรเจควิชาระบบปฏิบัติการ")
    canvas.restoreState()


def build(story):
    doc = SimpleDocTemplate(OUT, pagesize=A4, topMargin=2 * cm,
                            bottomMargin=2 * cm, leftMargin=2.2 * cm,
                            rightMargin=2.2 * cm,
                            title="PiCluster Report",
                            author="PiCluster Project")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def table(data, widths):
    t = Table(data, colWidths=widths)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Garuda-Bold", 10.5),
        ("FONT", (0, 1), (-1, -1), "Garuda", 10),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbe7f5")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def story():
    s = []

    # ---------- ปก ----------
    s.append(Spacer(1, 4.5 * cm))
    s.append(Paragraph("PiCluster", S_TITLE))
    s.append(Paragraph("คลัสเตอร์ Raspberry Pi 2 ตัว สำหรับให้บริการ Local LLM", S_SUB))
    s.append(Spacer(1, 1 * cm))
    s.append(Paragraph("รายงานโปรเจควิชาระบบปฏิบัติการ (Operating Systems)", S_SUB))
    s.append(Paragraph("ชั้นปีที่ 2 วิทยาการคอมพิวเตอร์", S_SUB))
    s.append(Spacer(1, 2.5 * cm))
    s.append(Paragraph("ผู้จัดทำ: [ใส่ชื่อ-รหัสนักศึกษา]", S_SUB))
    s.append(Paragraph("22 กันยายน 2026", S_SUB))
    s.append(PageBreak())

    # ---------- สารบัญ ----------
    s.append(H1("สารบัญ"))
    for i, t in enumerate([
            "1. บทนำ", "2. หลักการและทฤษฎีที่เกี่ยวข้อง",
            "3. ฮาร์ดแวร์ ซอฟต์แวร์ และโครงข่ายระบบ",
            "4. วิธีดำเนินการ", "5. ผลการทดลองและวิเคราะห์",
            "6. ปัญหาที่พบและการแก้ไข", "7. สรุปและงานต่อยอด",
            "ภาคผนวก ก: คำสั่งสำคัญที่ใช้ในระบบ"]):
        s.append(P(f"{t}"))
    s.append(PageBreak())

    # ---------- 1 ----------
    s.append(H1("1. บทนำ"))
    s.append(B("1.1 ที่มาและความสำคัญ"))
    s.append(P("ปัจจุบันการรันโมเดลภาษาขนาดเล็ก (small language model) บนเครื่อง "
               "single-board computer อย่าง Raspberry Pi เป็นสิ่งที่ทำได้จริง "
               "แต่ความสามารถของเครื่องเดียวจำกัดทั้งด้านหน่วยความจำและพลังประมวลผล "
               "โปรเจคนี้จึงตั้งคำถามเชิงระบบปฏิบัติการว่า: ถ้านำ Raspberry Pi มาต่อกัน"
               "เป็นคลัสเตอร์ จะช่วยการให้บริการโมเดล LLM ได้มากน้อยเพียงใด "
               "และช่วยในมิติใดของประสิทธิภาพกันแน่ — ช่วยให้ตอบเร็วขึ้น (latency) "
               "หรือรองรับผู้ใช้พร้อมกันได้มากขึ้น (throughput)"))
    s.append(B("1.2 วัตถุประสงค์"))
    s.append(P("1) สร้างระบบกระจายงานแบบ master–worker ที่เขียนเองทั้งหมดด้วย Python "
               "โดยไม่พึ่ง framework สำเร็จรูป เพื่อให้เห็นกลไก IPC, concurrency "
               "และ fault tolerance อย่างชัดเจน<br/>"
               "2) ใช้ local LLM ผ่าน llama.cpp เป็น workload จริง และวัดผลด้วย "
               "tokens ต่อวินาที<br/>"
               "3) ออกแบบการทดลองอย่างน้อย 3 สถานการณ์ เพื่อตอบคำถามข้อ 1)<br/>"
               "4) สร้างเครื่องมือประกอบ เช่น บอทรายงานสถานะผ่าน Telegram และ"
               "สคริปต์สร้างกราฟผลการทดลอง"))
    s.append(B("1.3 ขอบเขต"))
    s.append(P("ใช้ Raspberry Pi 4 Model B (RAM 2GB) จำนวน 2 เครื่อง ต่อกันด้วยสาย "
               "Ethernet ตรง, โมเดล Qwen2.5-Coder-0.5B แบบ quantize Q4_K_M "
               "และวัดผลเฉพาะขอบเขตการ generate ข้อความ (text generation) "
               "ไม่รวมการฝึกโมเดล"))

    # ---------- 2 ----------
    s.append(H1("2. หลักการและทฤษฎีที่เกี่ยวข้อง"))
    s.append(H2("2.1 คลัสเตอร์แบบ master–worker"))
    s.append(P("master ทำหน้าที่รับงานจาก client แล้วกระจายให้ worker ที่ว่าง "
               "โดยมีกลไกสามส่วน: (1) คิวงานกลาง (2) การจับคู่ job กับ free worker "
               "แบบ first-fit (3) การตรวจสุขภาพด้วย heartbeat — worker ส่งสัญญาณ"
               "ทุก 2 วินาที ถ้าเงียบเกิน 8 วินาทีถือว่าตาย และ job ที่ค้างอยู่จะถูก"
               "นำกลับคิวไปส่งใหม่ (requeue) สูงสุด 3 ครั้ง"))
    s.append(H2("2.2 ลักษณะของ LLM inference"))
    s.append(P("การประมวลผล LLM มีสองเฟส: prompt processing ซึ่งขนานได้ดีและ"
               "ผูกกับพลังคำนวณ (compute-bound) กับ text generation ซึ่งต้อง "
               "generate ทีละ token และทุก token ต้องอ่านน้ำหนัก (weights) ของ"
               "โมเดลจาก RAM หนึ่งรอบ จึงผูกกับแบนด์วิดท์ของหน่วยความจำ "
               "(memory-bandwidth bound) ข้อสรุปเชิงทฤษฎีคือ: การแบ่งโมเดล"
               "ข้ามเครื่องผ่านเครือข่ายที่ช้ากว่า RAM หลายสิบเท่า จะทำให้การ "
               "generate ช้าลง แต่การกระจาย request หลายชุดไปคนละเครื่อง "
               "จะเพิ่ม throughput ได้เกือบเชิงเส้น"))
    s.append(H2("2.3 Throughput กับ Latency"))
    s.append(P("คลัสเตอร์แบบกระจาย request ไม่สามารถทำให้คำตอบเดียวเร็วขึ้น "
               "แต่ทำให้ระบบรวม serve งานพร้อมกันได้มากขึ้น (aggregate throughput) "
               "การทดลองของโปรเจคนี้ออกแบบให้เห็นทั้งสองปรากฏการณ์พร้อมกัน"))

    # ---------- 3 ----------
    s.append(H1("3. ฮาร์ดแวร์ ซอฟต์แวร์ และโครงข่ายระบบ"))
    s.append(table([
        ["องค์ประกอบ", "รายละเอียด"],
        ["Node", "Raspberry Pi 4 Model B (RAM 2GB) จำนวน 2 เครื่อง, active cooling"],
        ["OS", "Raspberry Pi OS Lite 64-bit"],
        ["Inference engine", "llama.cpp สร้างจากซอร์ส (cmake, -j2), llama-server"],
        ["โมเดล", "Qwen2.5-Coder-0.5B-Instruct Q4_K_M (491,400,064 ไบต์)"],
        ["เครือข่าย", "Gigabit Ethernet ต่อตรง (subnet 10.0.0.0/24), WiFi สำหรับ SSH"],
        ["ภาษา", "Python 3 (stdlib ล้วน) สำหรับระบบกระจายงาน"],
    ], [4.5 * cm, 11.5 * cm]))
    s.append(Spacer(1, 6))
    s.append(P("โทโพโลยี: master ทำงานบน rpi4b-1 ร่วมกับ worker ของเครื่องนั้น "
               "worker อีกตัวทำงานบน rpi4b-2 ทั้งสอง worker เชื่อม master ที่ "
               "10.0.0.1 ผ่านสายตรง ทำให้ traffic ของคลัสเตอร์ทั้งหมดวิ่งบนสาย "
               "จุดต่อจุด ส่วน WiFi ใช้สำหรับการ SSH เข้าควบคุมงานเท่านั้น"))

    # ---------- 4 ----------
    s.append(H1("4. วิธีดำเนินการ"))
    s.append(H2("4.1 ระบบกระจายงาน"))
    s.append(P("ทุกข้อความระหว่าง master/worker/client เป็น JSON ที่ห่อด้วย "
               "4-byte length prefix ผ่าน TCP (โปรโตคอลกำหนดเองใน common.py) "
               "ข้อความหลักมี REGISTER, SUBMIT, JOB, JOB_RESULT, DONE และ "
               "HEARTBEAT master มี 4 ส่วนทำงานพร้อมกันด้วย thread: ตัวรับ"
               "การเชื่อมต่อ, dispatcher (จับคู่ job กับ worker ที่ว่าง), "
               "heartbeat listener และ failure monitor"))
    s.append(P("ระบบรับประกันการส่งงานแบบ at-least-once: job ที่ worker "
               "ขาดการเชื่อมต่อกลางทาง หรือ job ที่ worker รายงานว่าล้มเหลว "
               "จะถูกนำกลับเข้าคิวใหม่อัตโนมัติ (สูงสุด 3 ครั้ง) และ worker "
               "ที่หลุดการเชื่อมต่อจะพยายามกลับมาลงทะเบียนกับ master ใหม่"
               "ด้วยตัวเอง ข้อแลกเปลี่ยนคือในทางทฤษฎี job หนึ่งอาจถูกประมวลผล"
               "ซ้ำสองรอบในกรณีที่ node ถูกตัดสินว่าล้มทั้งที่ยังทำงานค้าง "
               "ซึ่งยอมรับได้สำหรับงาน inference เพราะผลลัพธ์คาดเดาได้ "
               "(temperature 0 และ seed เดียวกันให้ผลเดียวกัน)"))
    s.append(H2("4.2 วิธีวัดผลที่ควบคุมตัวแปร"))
    s.append(P("ใช้ llama-server ต่อ node, วัดด้วย client ตัวเดียวกันทุกเซ็ตอัป "
               "กำหนด seed 42, temperature 0, max_tokens 128, prompt เดียวกัน "
               "ทำ warm-up หนึ่งรอบก่อนเก็บค่า (เพื่อเลี่ยงผลของ page cache "
               "ที่ยังไม่ถูกอุ่น) และแยกอ่านค่า prompt processing กับ "
               "text generation ที่ llama-server รายงานมากับผลลัพธ์ของทุก job"))
    s.append(H2("4.3 สถานการณ์ทดลอง"))
    s.append(table([
        ["เซ็ตอัป", "การตั้งค่า", "ข้อสรุปที่ต้องการพิสูจน์"],
        ["A", "ลงทะเบียน worker เดียว ยิง 8 jobs",
         "baseline ของเครื่องเดียว"],
        ["B", "แบ่งน้ำหนักโมเดลข้ามเครื่องด้วย llama.cpp RPC",
         "การ split ไม่ช่วยความเร็วราย request"],
        ["C", "worker สองตัว ยิง 8 requests พร้อมกัน",
         "คลัสเตอร์เพิ่ม aggregate throughput"],
    ], [2 * cm, 7 * cm, 7 * cm]))

    # ---------- 5 ----------
    s.append(H1("5. ผลการทดลองและวิเคราะห์"))
    s.append(H2("5.1 เครือข่าย"))
    s.append(P("สายตรงระหว่างสอง node วัดด้วย iperf3 ได้ 943 Mbit/s "
               "(ค่าเต็มเชิงปฏิบัติของ gigabit) และ RTT เฉลี่ย 0.24 ms "
               "ยืนยันว่าเครือข่ายไม่ใช่คอขวดของเซ็ตอัป A และ C "
               "เพราะข้อมูลต่อ job มีขนาดระดับกิโลไบต์"))
    s.append(H2("5.2 Scenario A — เครื่องเดียว"))
    s.append(P("worker เพียงตัวเดียวรับงานทั้ง 8 jobs ต่อเนื่อง ใช้เวลารวม "
               "121.76 วินาที ได้ aggregate throughput 8.41 tok/s และทุก job "
               "ได้ความเร็วใกล้เคียงกันที่ ~8.4 tok/s ซึ่งเป็นความเร็ว "
               "เต็มของเครื่องเดียวกับโมเดลนี้"))
    s.append(H2("5.3 Scenario C — คลัสเตอร์สองเครื่อง"))
    s.extend(IMG(os.path.join(FIGS, "fig1_aggregate.png"),
                 "ภาพที่ 1: Aggregate throughput — 1 worker (8.41) เทียบ "
                 "2 workers (15.87) เทียบเส้นอุดมคติ 2 เท่า (16.82)"))
    s.append(P("8 jobs ถูกกระจายสลับสองเครื่องอย่างสมมาตร (4:4) ใช้เวลารวม "
               "64.51 วินาที ได้ aggregate 15.87 tok/s คิดเป็น scaling "
               "efficiency 94.3% เทียบอุดมคติ สิ่งสำคัญคือ tok/s ต่อ request "
               "ไม่ลดลงเลย (เฉลี่ย 8.30 เทียบกับ 8.43 ของเครื่องเดียว ต่างกัน "
               "1.5%) แปลว่าผู้ใช้ทุกคนยังได้ความเร็วเต็มเหมือนมีเครื่องส่วนตัว "
               "แต่ระบบรวม serve ได้มากขึ้นเกือบสองเท่า"))
    s.extend(IMG(os.path.join(FIGS, "fig2_per_job.png"),
                 "ภาพที่ 2: tok/s ราย job ของ scenario A และ C"))
    s.extend(IMG(os.path.join(FIGS, "fig3_walltime.png"),
                 "ภาพที่ 3: Wall time รวมของ 8 jobs — 121.76s เทียบ 64.51s"))
    s.append(H2("5.4 Scenario B — การแบ่งโมเดลข้ามเครื่อง"))
    if os.path.isfile(os.path.join(BASE, "results", "scenario_B.txt")):
        s.append(P("การแบ่งน้ำหนักโมเดลข้ามเครื่องด้วย RPC ทำงานได้จริง (4/4 jobs "
                   "สำเร็จ) แต่ให้ความเร็วเฉลี่ยเพียง 7.62 tok/s ต่อ request "
                   "เทียบกับ 8.43 tok/s ของเครื่องเดียว — ช้าลงราว 10% "
                   "แม้จะใช้ลิงก์ gigabit (943 Mbit/s) ก็ตาม เพราะทุก token "
                   "ที่ generate ต้องมีการสื่อสารเลเยอร์ข้ามเครือข่าย "
                   "ถ้าเป็นลิงก์ 100 Mbit/s ความต่างจะยิ่งมากกว่านี้มาก "
                   "ดูภาพที่ 4"))
        s.extend(IMG(os.path.join(FIGS, "fig4_split.png"),
                     "ภาพที่ 4: เครื่องเดียว (8.43 tok/s) เทียบ การแบ่งโมเดล"
                     "ข้ามเครื่อง (7.62 tok/s)"))
    else:
        s.append(P("(กำลังดำเนินการ — ดูผลในภาคผนวกและไฟล์ "
                   "results/scenario_B.txt)"))
    s.append(H2("5.5 การอภิปราย"))
    s.append(P("ผลการทดลองทั้งสามชุดประกอบกันตอบคำถามตั้งต้นของโปรเจคอย่างครบถ้วน: "
               "คลัสเตอร์ไม่ได้ทำให้คำตอบเดียวเร็วขึ้น (และการแบ่งโมเดลแบบผสม"
               "เครื่องธรรมดากลับทำให้แย่ลง) แต่ทำให้ระบบรวมรองรับผู้ใช้พร้อมกัน"
               "ได้เกือบสองเท่า ซึ่งตรงกับหลักที่ว่า LLM inference แบบ generate "
               "ทีละ token เป็นงานผูกกับแบนด์วิดท์หน่วยความจำเป็นหลัก "
               "และตรงกับแนวปฏิบัติจริงในอุตสาหกรรมที่ขยายความจุด้วยการเพิ่ม"
               "จำนวนเครื่องให้บริการ (replica) มากกว่าการแบ่งโมเดลข้ามเครื่อง"))

    # ---------- 6 ----------
    s.append(H1("6. ปัญหาที่พบและการแก้ไข"))
    s.append(table([
        ["ปัญหา", "สาเหตุ", "การแก้ไข"],
        ["build llama.cpp ถูก kill กลางทาง",
         "RAM 2GB ไม่พอตอน compile",
         "เพิ่ม swap เป็น 2GB และ build ด้วย -j2"],
        ["SSH หลุดเป็นระยะ",
         "WiFi 2.4GHz มีสัญญาณรบกวน",
         "ย้าย traffic คลัสเตอร์ลงสาย Ethernet ทั้งหมด"],
        ["ประสิทธิภาพตกแปลก ๆ บนเครื่องหนึ่ง",
         "PSU ให้ไฟไม่เสถียร (undervoltage) — ตรวจด้วย vcgencmd get_throttled",
         "เปลี่ยน PSU 5.1V/3A ค่า throttle กลับเป็น 0x0"],
        ["ลิงก์สายตรงได้แค่ 100 Mbit/s",
         "สาย LAN ขาดคู่สาย (gigabit ต้องใช้ครบ 4 คู่)",
         "เปลี่ยนสายใหม่ ได้ 943 Mbit/s"],
        ["โหลดโมเดลค้างไม่คืบ",
         "wget -c -O resume ไฟล์ไม่ถูกต้อง",
         "คัดลอกไฟล์จาก node ที่สมบูรณ์ผ่านสายตรง (491MB ~16 วิ)"],
        ["worker ตายเป๊ะทุก 5 วินาที",
         "socket timeout=5 ตอนเชื่อม master ไม่ได้เคลียร์ก่อนลูปรับงาน",
         "เรียก conn.settimeout(None) หลังเชื่อมต่อสำเร็จ"],
        ["rerun benchmark ได้ค่าตกเพราะวิ่งทับ compile",
         "build กิน CPU ทั้ง 4 คอร์แย่งกับ inference",
         "กำหนดให้เก็บตัวเลขเฉพาะช่วงที่ระบบว่างเท่านั้น"],
        ["รีบูตถี่ ๆ แล้ว node ที่บูตจาก USB หายจากเครือข่าย",
         "USB boot device ไม่ re-enumerate ทันเมื่อรีบูตติดกันเร็ว ๆ",
         "ถอดปลั๊กรอ ≥30 วิ และตั้ง PROGRAM_USB_BOOT_TIMEOUT=1 "
         "ใน EEPROM ยืดเวลารอ USB ตอนบูต"],
    ], [4.5 * cm, 5.5 * cm, 6 * cm]))

    # ---------- 7 ----------
    s.append(H1("7. สรุปและงานต่อยอด"))
    s.append(P("โปรเจคสร้างระบบคลัสเตอร์จริงที่ประกอบจาก: ระบบกระจายงานที่เขียน"
               "เองพร้อม fault tolerance, การเชื่อมโครงข่ายสองระดับ (สายตรงสำหรับ"
               "traffic, WiFi สำหรับควบคุม), การวัดผลที่ควบคุมตัวแปร และเครื่องมือ"
               "เสริม (Telegram bot รายงานสถานะ, สคริปต์กราฟอัตโนมัติ) ข้อสรุป"
               "เชิงระบบคือ: คลัสเตอร์ Pi สองตัวเพิ่ม throughput ของการให้บริการ "
               "LLM ได้ 1.89 เท่า (94.3% ของอุดมคติ) โดยไม่ลดคุณภาพราย request "
               "แต่ไม่สามารถเร่งคำตอบเดียวได้ และการแบ่งโมเดลข้ามเครื่องยิ่ง"
               "ทำให้ช้าลง"))
    s.append(B("งานต่อยอด"))
    s.append(P("1) ย้ายบอทและ master ขึ้น systemd เพื่อรันถาวร<br/>"
               "2) เพิ่มการกระจายแบบ weighted ตามความเร็วจริงของแต่ละ node<br/>"
               "3) ทดสอบกับโมเดลใหญ่ขึ้น (7B) บน Pi 5 RAM 4GB เพื่อศึกษา"
               "กรณีโมเดลใหญ่เกิน RAM เดี่ยว<br/>"
               "4) ใช้ cgroups แยก CPU share ระหว่าง inference กับ workload อื่น<br/>"
               "5) เพิ่มการยืนยันตัวตนให้ master (ข้อจำกัดปัจจุบัน: อุปกรณ์ใด ๆ "
               "บน LAN สามารถส่ง job หรือปลอมเป็น worker ได้)"))

    # ---------- appendix ----------
    s.append(PageBreak())
    s.append(H1("ภาคผนวก ก: คำสั่งสำคัญ"))
    s.append(Paragraph(
        "# เริ่มระบบทั้งหมด (บนแต่ละเครื่อง)<br/>"
        "~/llama.cpp/build/bin/llama-server -m ~/models/qwen2.5-coder-0.5b-instruct-q4_k_m.gguf "
        "--host 127.0.0.1 --port 8080 -c 512 -t 4<br/><br/>"
        "# master (บน rpi4b-1)<br/>"
        "python3 master.py<br/><br/>"
        "# worker (บนแต่ละ Pi)<br/>"
        "python3 worker.py --master 10.0.0.1 --name &lt;ชื่อเครื่อง&gt; --port 8080<br/><br/>"
        "# ยิงงาน<br/>"
        "python3 client.py --master 10.0.0.1 --requests 8 --max-tokens 128<br/><br/>"
        "# วัดเครือข่าย<br/>"
        "iperf3 -c 10.0.0.2 -t 5 ; ping -c 5 10.0.0.2<br/><br/>"
        "# ตรวจสุขภาพฮาร์ดแวร์<br/>"
        "vcgencmd get_throttled ; vcgencmd measure_temp", S_MONO))
    return s


os.makedirs(os.path.dirname(OUT), exist_ok=True)
build(story())
print("PDF written:", OUT)
