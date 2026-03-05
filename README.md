# LINE Bot ขอข้อมูลผู้จดทะเบียนเบอร์โทรศัพท์ (CCIB)

## ภาพรวมระบบ

Bot สำหรับกรอกแบบฟอร์มขอข้อมูลผู้จดทะเบียนเบอร์โทรศัพท์ผ่าน LINE
โดยเชื่อมต่อกับระบบ CCIB (Cyber Crime Investigation Bureau)

### ขั้นตอนการทำงาน

```
ผู้ใช้พิมพ์ "ขอข้อมูล"
    ↓
กรอกเบอร์โทรศัพท์
    ↓
เลือกประเภทคำขอ (ความผิดทางเทคโนโลยี / เหตุต้องสงสัย)
    ↓
เลือกช่องทางร้องเรียน (ตำรวจ / ค่ายมือถือ / อื่นๆ)
    ↓
เลือกประเภทความผิด (โทรศัพท์ / SMS)
    ↓
กรอกเบอร์ผู้รับ
    ↓
กรอกเบอร์ผู้โทร
    ↓
กรอกวันเวลาเกิดเหตุ
    ↓
แสดงสรุป Flex Message → ยืนยัน/ยกเลิก
    ↓
ส่งข้อมูลไป CCIB API
```

## การติดตั้ง

### 1. ติดตั้ง Dependencies

```bash
pip install -r requirements.txt
```

### 2. ตั้งค่า LINE Bot

1. สร้าง LINE Bot ที่ [LINE Developers Console](https://developers.line.biz/)
2. เปิดใช้ Messaging API
3. คัดลอก **Channel Access Token** และ **Channel Secret**

### 3. ตั้งค่า Environment Variables

```bash
cp .env.example .env
# แก้ไขค่าใน .env
```

### 4. รัน Server

```bash
# Development
python app.py

# Production
gunicorn app:app -b 0.0.0.0:5000 -w 4
```

### 5. ตั้งค่า Webhook URL

ใน LINE Developers Console ตั้ง Webhook URL เป็น:
```
https://your-domain.com/callback
```

## คำสั่งที่ใช้ได้

| คำสั่ง | การทำงาน |
|--------|----------|
| `ขอข้อมูล` / `เริ่ม` / `/start` | เริ่มกรอกแบบฟอร์ม |
| `ยกเลิก` | ยกเลิกคำขอปัจจุบัน |
| `ยืนยัน` | ยืนยันส่งข้อมูล |

## ⚠️ ข้อควรระวัง

- ระบบนี้ใช้เฉพาะเจ้าหน้าที่ที่ได้รับอนุญาตเท่านั้น
- ต้องมี session cookie ที่ได้รับจากการ login ระบบ CCIB อย่างถูกต้อง
- ควรตรวจสอบนโยบายการใช้ API กับหน่วยงานก่อนนำไปใช้งาน
