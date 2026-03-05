"""
LINE Bot สำหรับขอข้อมูลผู้จดทะเบียนเบอร์โทรศัพท์ผ่านระบบ CCIB
=================================================================
Model: tj_ccib_5c.telco_request
Endpoint: https://ccib.cyberpolice.go.th/web/dataset/call_kw/...

⚠️ หมายเหตุสำคัญ:
- ต้องได้รับสิทธิ์เข้าถึงระบบ CCIB อย่างถูกต้อง
- ต้องมี session_id ที่ได้รับจากการ login
- ใช้เฉพาะเจ้าหน้าที่ที่ได้รับมอบหมายเท่านั้น
"""

import os
import json
import logging
from datetime import datetime
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    FlexMessage,
    FlexContainer,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import requests as http_requests

# ===================== CONFIG =====================
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# LINE Bot credentials
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "YOUR_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "YOUR_SECRET")

# CCIB Config
CCIB_BASE_URL = "https://ccib.cyberpolice.go.th"
CCIB_SESSION_ID = os.getenv("CCIB_SESSION_ID", "")

# Odoo Model
ODOO_MODEL = "tj_ccib_5c.telco_request"

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ===================== USER SESSION STORE =====================
user_sessions = {}

# ===================== FORM OPTIONS =====================
REQUEST_TYPES = {
    "1": {"value": "RQ-CYCR", "label": "ความผิดทางเทคโนโลยี"},
    "2": {"value": "RQ-SUSP", "label": "เหตุต้องสงสัย"},
}

COMPLAIN_VIA = {
    "1": {"value": "CP-PL", "label": "ร้องฯผ่านตำรวจ"},
    "2": {"value": "CP-TC", "label": "ร้องฯผ่านค่ายมือถือ"},
    "3": {"value": "CP-OT", "label": "ร้องฯผ่านทางอื่นๆ"},
}

OFFENCE_TYPES = {
    "1": {"value": "OF-CAL", "label": "โทรศัพท์"},
    "2": {"value": "OF-SMS", "label": "SMS"},
}

# ===================== FORM STEPS =====================
FORM_STEPS = [
    {
        "field": "mobile_no",
        "prompt": "📱 กรุณากรอก เบอร์โทรศัพท์ ที่ต้องการขอข้อมูล:",
        "type": "text",
        "validate": lambda x: x.replace("-", "").replace(" ", "").isdigit()
        and 9 <= len(x.replace("-", "").replace(" ", "")) <= 10,
        "error_msg": "❌ กรุณากรอกเบอร์โทรศัพท์ให้ถูกต้อง (9-10 หลัก)",
    },
    {
        "field": "request_type",
        "prompt": "📋 เลือก ประเภทคำขอ:",
        "type": "choice",
        "options": REQUEST_TYPES,
    },
    {
        "field": "complain_via",
        "prompt": "📝 เลือก ช่องทางการร้องเรียน:",
        "type": "choice",
        "options": COMPLAIN_VIA,
    },
    {
        "field": "offence_type",
        "prompt": "⚖️ เลือก ประเภทความผิด:",
        "type": "choice",
        "options": OFFENCE_TYPES,
    },
    {
        "field": "number_receiver",
        "prompt": "📞 กรุณากรอก เบอร์ผู้รับ:",
        "type": "text",
        "validate": lambda x: x.replace("-", "").replace(" ", "").isdigit()
        and 9 <= len(x.replace("-", "").replace(" ", "")) <= 10,
        "error_msg": "❌ กรุณากรอกเบอร์โทรศัพท์ให้ถูกต้อง",
    },
    {
        "field": "number_caller",
        "prompt": "📞 กรุณากรอก เบอร์ผู้โทร:",
        "type": "text",
        "validate": lambda x: x.replace("-", "").replace(" ", "").isdigit()
        and 9 <= len(x.replace("-", "").replace(" ", "")) <= 10,
        "error_msg": "❌ กรุณากรอกเบอร์โทรศัพท์ให้ถูกต้อง",
    },
    {
        "field": "offence_date_time",
        "prompt": "📅 กรุณากรอก วันเวลาเกิดเหตุ\n(รูปแบบ: DD/MM/YYYY HH:MM)\nเช่น: 15/03/2026 14:30",
        "type": "text",
        "validate": lambda x: validate_datetime(x),
        "error_msg": "❌ รูปแบบวันที่ไม่ถูกต้อง กรุณาใช้: DD/MM/YYYY HH:MM",
    },
]


def validate_datetime(dt_str):
    try:
        datetime.strptime(dt_str.strip(), "%d/%m/%Y %H:%M")
        return True
    except ValueError:
        return False


def convert_datetime_to_odoo(dt_str):
    """แปลงวันที่จาก DD/MM/YYYY HH:MM → YYYY-MM-DD HH:MM:SS (Odoo format)"""
    dt = datetime.strptime(dt_str.strip(), "%d/%m/%Y %H:%M")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ===================== SESSION MANAGEMENT =====================
def get_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = {"active": False, "step": 0, "data": {}}
    return user_sessions[user_id]


def reset_session(user_id):
    user_sessions[user_id] = {"active": False, "step": 0, "data": {}}


# ===================== ODOO RPC CLIENT =====================
class OdooRPC:
    """
    Odoo JSON-RPC Client สำหรับระบบ CCIB
    จำลองพฤติกรรมเดียวกับ Web UI ที่เรียก:
    - /web/dataset/call_kw/{model}/onchange
    - /web/dataset/call_kw/{model}/create
    - /web/dataset/call_kw/{model}/write
    - /ks_app_frequency/update
    """

    def __init__(self, base_url, session_id):
        self.base_url = base_url
        self.session_id = session_id
        self._rpc_id = 0

    def _next_id(self):
        self._rpc_id += 1
        return self._rpc_id

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "Cookie": f"session_id={self.session_id}",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/web",
        }

    def _field_spec(self):
        """
        Field specification ของ model tj_ccib_5c.telco_request
        (ตรงตาม onchange request ที่ดักจากหน้าเว็บ)
        """
        return {
            "state": "",
            "agency_refid": "",
            "mobile_no": "",
            "doc_no_witness_subp": "",
            "create_uid": "",
            "create_date": "",
            "approve_datetime": "",
            "approver_user_id": "",
            "complain_other_desc": "",
            "complain_via": "",
            "helpdesk_ticket_id": "",
            "nbtc_called_datetime": "",
            "nbtc_called_ready": "",
            "nbtc_called_ref": "",
            "nbtc_resp_detail": "",
            "nbtc_status_code": "",
            "number_caller": "",
            "number_receiver": "",
            "offence_date_time": "",
            "offence_type": "",
            "receive_datetime": "",
            "reconcile_code": "",
            "request_type": "",
            "telco_code_fr_reply": "",
            "telco_subregbymobile_ids": "",
            "telco_subregbymobile_ids.agency_refid": "",
            "telco_subregbymobile_ids.current_location": "",
            "telco_subregbymobile_ids.first_name": "",
            "telco_subregbymobile_ids.id_card": "",
            "telco_subregbymobile_ids.last_name": "",
            "telco_subregbymobile_ids.mobile_no": "",
            "telco_subregbymobile_ids.register_date": "",
            "telco_subregbymobile_ids.telco_code": "",
            "telco_subregbymobile_latest_id": "",
            "tpo_case_id": "",
        }

    def _context(self):
        return {
            "lang": "th_TH",
            "tz": "Asia/Bangkok",
            "allowed_company_ids": [1],
        }

    def _post(self, url, payload):
        """HTTP POST ทั่วไป พร้อม error handling"""
        try:
            resp = http_requests.post(
                url, json=payload, headers=self._headers(), timeout=30
            )
            resp.raise_for_status()
            result = resp.json()

            # ตรวจสอบ Odoo error response
            if result.get("error"):
                err_data = result["error"].get("data", {})
                err_msg = err_data.get("message", result["error"].get("message", "Unknown"))
                logger.error(f"Odoo Error: {err_msg}")
                return {"success": False, "error": err_msg}

            return {"success": True, "data": result.get("result")}
        except http_requests.exceptions.Timeout:
            return {"success": False, "error": "Request timeout"}
        except http_requests.exceptions.ConnectionError:
            return {"success": False, "error": "Cannot connect to CCIB server"}
        except Exception as e:
            logger.error(f"RPC Error: {e}")
            return {"success": False, "error": str(e)}

    # ──────────── API Methods ────────────

    def onchange(self, values=None):
        """
        เรียก onchange (จำลองการกรอกฟอร์มบนหน้าเว็บ)
        POST /web/dataset/call_kw/tj_ccib_5c.telco_request/onchange
        """
        url = f"{self.base_url}/web/dataset/call_kw/{ODOO_MODEL}/onchange"

        payload = {
            "id": self._next_id(),
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "args": [
                    [],                     # ids (ว่างสำหรับ record ใหม่)
                    values or {},           # ค่าที่กรอก
                    [],                     # changed fields
                    self._field_spec(),     # field spec ทั้งหมด
                ],
                "model": ODOO_MODEL,
                "method": "onchange",
                "kwargs": {"context": self._context()},
            },
        }

        logger.info(f"[onchange] Calling with values: {values}")
        return self._post(url, payload)

    def create(self, values):
        """
        สร้าง record ใหม่
        POST /web/dataset/call_kw/tj_ccib_5c.telco_request/create
        """
        url = f"{self.base_url}/web/dataset/call_kw/{ODOO_MODEL}/create"

        payload = {
            "id": self._next_id(),
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "args": [values],
                "model": ODOO_MODEL,
                "method": "create",
                "kwargs": {"context": self._context()},
            },
        }

        logger.info(f"[create] Creating record...")
        return self._post(url, payload)

    def write(self, record_id, values):
        """
        อัปเดต record
        POST /web/dataset/call_kw/tj_ccib_5c.telco_request/write
        """
        url = f"{self.base_url}/web/dataset/call_kw/{ODOO_MODEL}/write"

        payload = {
            "id": self._next_id(),
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "args": [[record_id], values],
                "model": ODOO_MODEL,
                "method": "write",
                "kwargs": {"context": self._context()},
            },
        }

        logger.info(f"[write] Updating record {record_id}...")
        return self._post(url, payload)

    def ks_update(self, record_id, values):
        """
        เรียก /ks_app_frequency/update
        POST /ks_app_frequency/update
        """
        url = f"{self.base_url}/ks_app_frequency/update"

        payload = {
            "id": self._next_id(),
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "record_id": record_id,
                "values": values,
            },
        }

        logger.info(f"[ks_update] Updating record {record_id}...")
        return self._post(url, payload)


# สร้าง RPC client
odoo = OdooRPC(CCIB_BASE_URL, CCIB_SESSION_ID)


# ===================== SUBMIT WORKFLOW =====================
def submit_to_ccib(form_data):
    """
    Workflow จำลองพฤติกรรมเว็บ UI:

    1) กดปุ่ม "ใหม่"    → POST onchange({})         → รับ default values
    2) กรอกข้อมูล       → POST onchange(values)     → validate
    3) กด "บันทึก"      → POST create(values)       → สร้าง record ได้ ID
    4) (ถ้ามี)          → POST /ks_app_frequency/update → อัปเดตเพิ่ม
    """

    # แปลงวันที่
    odoo_datetime = ""
    if form_data.get("offence_date_time"):
        odoo_datetime = convert_datetime_to_odoo(form_data["offence_date_time"])

    values = {
        "mobile_no": form_data.get("mobile_no", ""),
        "request_type": form_data.get("request_type", ""),
        "complain_via": form_data.get("complain_via", ""),
        "offence_type": form_data.get("offence_type", ""),
        "number_receiver": form_data.get("number_receiver", ""),
        "number_caller": form_data.get("number_caller", ""),
        "offence_date_time": odoo_datetime,
    }

    # ── Step 1: กดปุ่ม "ใหม่" ──
    logger.info("═══ Step 1/4: Simulating 'New' button ═══")
    r1 = odoo.onchange()
    if not r1["success"]:
        return {"success": False, "error": f"[New] {r1['error']}"}

    # merge default values ที่ server ส่งกลับมา
    defaults = {}
    if r1.get("data") and isinstance(r1["data"], dict):
        defaults = r1["data"].get("value", {})

    # ── Step 2: กรอกข้อมูล → onchange ──
    logger.info("═══ Step 2/4: Onchange validation ═══")
    r2 = odoo.onchange(values)
    if not r2["success"]:
        return {"success": False, "error": f"[Validate] {r2['error']}"}

    # ตรวจ warning
    if r2.get("data") and isinstance(r2["data"], dict):
        warning = r2["data"].get("warning")
        if warning:
            logger.warning(f"Onchange warning: {warning}")

        # merge onchange values
        onchange_vals = r2["data"].get("value", {})
        if onchange_vals:
            defaults.update(onchange_vals)

    # รวมค่าทั้งหมด: defaults + onchange + user input
    final_values = {**defaults, **values}

    # ลบค่าที่เป็น empty string หรือ False ที่ไม่จำเป็น
    create_values = {
        k: v for k, v in final_values.items()
        if v not in ("", False, None) and not k.startswith("telco_subregbymobile_ids.")
    }

    # ── Step 3: Create record ──
    logger.info("═══ Step 3/4: Creating record ═══")
    r3 = odoo.create(create_values)
    if not r3["success"]:
        return {"success": False, "error": f"[Create] {r3['error']}"}

    record_id = r3.get("data")
    logger.info(f"✅ Record created: ID = {record_id}")

    # ── Step 4: /ks_app_frequency/update ──
    if record_id:
        logger.info("═══ Step 4/4: ks_app_frequency/update ═══")
        r4 = odoo.ks_update(record_id, create_values)
        if not r4["success"]:
            logger.warning(f"ks_update non-critical error: {r4['error']}")

    return {"success": True, "record_id": record_id}


# ===================== FLEX MESSAGE BUILDERS =====================
def build_summary_flex(data):
    req_label = next(
        (v["label"] for v in REQUEST_TYPES.values() if v["value"] == data.get("request_type")),
        data.get("request_type", "-"),
    )
    comp_label = next(
        (v["label"] for v in COMPLAIN_VIA.values() if v["value"] == data.get("complain_via")),
        data.get("complain_via", "-"),
    )
    off_label = next(
        (v["label"] for v in OFFENCE_TYPES.values() if v["value"] == data.get("offence_type")),
        data.get("offence_type", "-"),
    )

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "สรุปคำขอข้อมูลผู้จดทะเบียน",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#ffffff",
                },
                {
                    "type": "text",
                    "text": f"Model: {ODOO_MODEL}",
                    "size": "xxs",
                    "color": "#ffffffaa",
                    "margin": "xs",
                },
            ],
            "backgroundColor": "#1a237e",
            "paddingAll": "18px",
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "18px",
            "contents": [
                _flex_row("เบอร์โทรศัพท์", data.get("mobile_no", "-")),
                {"type": "separator", "margin": "sm"},
                _flex_row("ประเภทคำขอ", req_label),
                {"type": "separator", "margin": "sm"},
                _flex_row("ช่องทางร้องเรียน", comp_label),
                {"type": "separator", "margin": "sm"},
                _flex_row("ประเภทความผิด", off_label),
                {"type": "separator", "margin": "sm"},
                _flex_row("เบอร์ผู้รับ", data.get("number_receiver", "-")),
                {"type": "separator", "margin": "sm"},
                _flex_row("เบอร์ผู้โทร", data.get("number_caller", "-")),
                {"type": "separator", "margin": "sm"},
                _flex_row("วันเวลาเกิดเหตุ", data.get("offence_date_time", "-")),
            ],
        },
        "footer": {
            "type": "box",
            "layout": "horizontal",
            "spacing": "md",
            "paddingAll": "15px",
            "contents": [
                {
                    "type": "button",
                    "action": {"type": "message", "label": "ยืนยันส่ง", "text": "ยืนยัน"},
                    "style": "primary",
                    "color": "#1a237e",
                    "height": "sm",
                },
                {
                    "type": "button",
                    "action": {"type": "message", "label": "ยกเลิก", "text": "ยกเลิก"},
                    "style": "secondary",
                    "height": "sm",
                },
            ],
        },
    }


def build_result_flex(record_id, form_data):
    return {
        "type": "bubble",
        "size": "kilo",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "ส่งคำขอสำเร็จ ✓",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#ffffff",
                }
            ],
            "backgroundColor": "#2e7d32",
            "paddingAll": "18px",
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "18px",
            "contents": [
                _flex_row("Record ID", str(record_id)),
                {"type": "separator", "margin": "sm"},
                _flex_row("เบอร์ที่ขอ", form_data.get("mobile_no", "-")),
                {"type": "separator", "margin": "sm"},
                _flex_row("สถานะ", "รอดำเนินการ"),
                {
                    "type": "text",
                    "text": f"ส่งเมื่อ: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
                    "size": "xs",
                    "color": "#888888",
                    "margin": "lg",
                    "align": "center",
                },
            ],
        },
    }


def _flex_row(label, value):
    return {
        "type": "box",
        "layout": "horizontal",
        "contents": [
            {"type": "text", "text": label, "size": "sm", "color": "#666666", "flex": 4},
            {"type": "text", "text": str(value), "size": "sm", "weight": "bold", "flex": 6, "wrap": True},
        ],
    }


def build_quick_reply_options(options):
    items = []
    for key, opt in options.items():
        items.append(
            QuickReplyItem(action=MessageAction(label=opt["label"][:20], text=key))
        )
    return QuickReply(items=items)


# ===================== LINE WEBHOOK =====================
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    logger.info(f"Webhook received: {body[:200]}")

    try:
        handler.handle(body, signature)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()
    session = get_session(user_id)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        # ==================== คำสั่งเริ่มต้น ====================
        if text in ["ขอข้อมูล", "เริ่ม", "ใหม่", "/start"]:
            session["active"] = True
            session["step"] = 0
            session["data"] = {}
            session["pending_submit"] = False

            step = FORM_STEPS[0]
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[
                        TextMessage(
                            text=(
                                "กรอกแบบฟอร์มขอข้อมูลผู้จดทะเบียน\n"
                                "━━━━━━━━━━━━━━━━━━━\n"
                                "พิมพ์ 'ยกเลิก' ได้ตลอดเวลา\n\n"
                                f"ขั้นตอนที่ 1/{len(FORM_STEPS)}\n"
                                + step["prompt"]
                            )
                        )
                    ],
                )
            )
            return

        # ==================== ยกเลิก ====================
        if text == "ยกเลิก":
            reset_session(user_id)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="❌ ยกเลิกคำขอเรียบร้อย")],
                )
            )
            return

        # ==================== ยืนยันส่งข้อมูล ====================
        if text == "ยืนยัน" and session.get("pending_submit"):
            # ส่งข้อมูลไป CCIB
            result = submit_to_ccib(session["data"])

            if result["success"]:
                record_id = result.get("record_id", "N/A")
                flex_json = build_result_flex(record_id, session["data"])
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            FlexMessage(
                                alt_text=f"ส่งสำเร็จ - Record #{record_id}",
                                contents=FlexContainer.from_json(json.dumps(flex_json)),
                            )
                        ],
                    )
                )
            else:
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            TextMessage(
                                text=f"❌ ส่งคำขอไม่สำเร็จ\nข้อผิดพลาด: {result['error']}"
                            )
                        ],
                    )
                )

            reset_session(user_id)
            return

        # ==================== กรอกฟอร์ม ====================
        if not session["active"]:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[
                        TextMessage(
                            text=(
                                "👋 สวัสดีครับ — ระบบขอข้อมูลผู้จดทะเบียน CCIB\n\n"
                                "พิมพ์ 'ขอข้อมูล' เพื่อเริ่มกรอกแบบฟอร์ม"
                            ),
                            quick_reply=QuickReply(
                                items=[
                                    QuickReplyItem(
                                        action=MessageAction(label="ขอข้อมูล", text="ขอข้อมูล")
                                    )
                                ]
                            ),
                        )
                    ],
                )
            )
            return

        current_step_idx = session["step"]
        if current_step_idx >= len(FORM_STEPS):
            return

        current_step = FORM_STEPS[current_step_idx]

        # === Validate & Store ===
        if current_step["type"] == "choice":
            options = current_step["options"]
            if text not in options:
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            TextMessage(
                                text="❌ กรุณาเลือกตัวเลือกที่ถูกต้อง",
                                quick_reply=build_quick_reply_options(options),
                            )
                        ],
                    )
                )
                return
            session["data"][current_step["field"]] = options[text]["value"]

        elif current_step["type"] == "text":
            validator = current_step.get("validate")
            if validator and not validator(text):
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=current_step["error_msg"])],
                    )
                )
                return
            session["data"][current_step["field"]] = text.strip()

        # === Next Step ===
        session["step"] += 1
        next_idx = session["step"]

        if next_idx < len(FORM_STEPS):
            next_step = FORM_STEPS[next_idx]
            prompt_text = f"ขั้นตอนที่ {next_idx + 1}/{len(FORM_STEPS)}\n{next_step['prompt']}"

            if next_step["type"] == "choice":
                messages = [
                    TextMessage(
                        text=prompt_text,
                        quick_reply=build_quick_reply_options(next_step["options"]),
                    )
                ]
            else:
                messages = [TextMessage(text=prompt_text)]

            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=messages)
            )
        else:
            # === กรอกครบ → แสดงสรุป ===
            session["pending_submit"] = True
            flex_json = build_summary_flex(session["data"])

            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[
                        FlexMessage(
                            alt_text="สรุปคำขอข้อมูลผู้จดทะเบียน",
                            contents=FlexContainer.from_json(json.dumps(flex_json)),
                        )
                    ],
                )
            )


# ===================== HEALTH CHECK =====================
@app.route("/health", methods=["GET"])
def health():
    return {
        "status": "ok",
        "model": ODOO_MODEL,
        "ccib_url": CCIB_BASE_URL,
        "session_active": bool(CCIB_SESSION_ID),
        "timestamp": datetime.now().isoformat(),
    }


# ===================== MAIN =====================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    logger.info(f"Starting CCIB LINE Bot on port {port}")
    logger.info(f"Model: {ODOO_MODEL}")
    logger.info(f"CCIB: {CCIB_BASE_URL}")
    logger.info(f"Session configured: {'Yes' if CCIB_SESSION_ID else 'No'}")
    app.run(host="0.0.0.0", port=port, debug=True)
