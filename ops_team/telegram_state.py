"""Durable business facts and delivery metadata; no raw chats or model payloads."""
import hashlib
import base64
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import math

ROOT = Path(__file__).resolve().parents[1]/".telegram_state"
GROUP_ID = -5395485115
FIELDS = {"customer", "warehouses", "period", "cutoff", "report_time", "timezone", "goals", "contacts",
          "kpi_formulas", "mapping", "numeric_format", "source_id", "sheets_id", "sheets_ranges", "sheets_tab", "sheets_gid"}
REQUIRED = ("customer", "warehouses", "period", "cutoff", "report_time", "timezone", "goals", "contacts")
LABELS = {"customer": "Khách hàng/phạm vi", "warehouses": "Kho trong phạm vi (cách nhau bằng dấu phẩy)",
          "period": "Kỳ dữ liệu YYYY-MM", "cutoff": "Giờ chốt dữ liệu HH:MM", "report_time": "Giờ nhận báo cáo HH:MM",
          "timezone": "Múi giờ (Asia/Ho_Chi_Minh)", "goals": "Mục tiêu và ngưỡng cần theo dõi",
          "contacts": "Đầu mối xác nhận dữ liệu", "kpi_formulas": "Định nghĩa KPI (ghi nhớ; chỉ công thức code đã kiểm tra được tính)",
          "mapping": "Ánh xạ cột JSON", "numeric_format": "Định dạng số machine/vi/en", "source_id": "Mã nguồn đã chọn",
          "sheets_id": "Link hoặc ID Google Sheets", "sheets_ranges": "JSON bảng → tab/range A1",
          "sheets_tab": "Tên tab được phép đọc", "sheets_gid": "Sheet ID/gid đã xác minh"}
PRIVATE_CONTROL = {"paired", "approved_plan", "pending_field"}


def protect_text(text):
    from .telegram_secrets import _dpapi
    return "DPAPI1:"+base64.b64encode(_dpapi(text.encode())).decode()


def unprotect_text(text):
    from .telegram_secrets import _dpapi
    if not text.startswith("DPAPI1:"):
        raise ValueError("Giá trị bền chưa được mã hóa DPAPI.")
    return _dpapi(base64.b64decode(text[7:]), decrypt=True).decode()


def private_int(value):
    return protect_text(str(value))


def read_private_int(value):
    return int(unprotect_text(value))


def contains_secret(text, known=()):
    return (any(s and s in text for s in known) or
            bool(re.search(r"(?:[0-9]{5,16}:[A-Za-z0-9_-]{20,100}|gsk_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA )?PRIVATE KEY-----)", text)))


def validate_fact(key, value):
    if key not in FIELDS or not isinstance(value, str) or not 1 <= len(value.strip()) <= 1200:
        raise ValueError("Trường bộ nhớ chưa hỗ trợ hoặc giá trị vượt 1.200 ký tự.")
    value = value.strip()
    if contains_secret(value):
        raise ValueError("Không lưu khóa/bí mật vào bộ nhớ nghiệp vụ.")
    if key in ("cutoff", "report_time") and not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError("Giờ phải theo HH:MM, từ 00:00 đến 23:59.")
    if key == "period" and not re.fullmatch(r"20[0-9]{2}-(?:0[1-9]|1[0-2])", value):
        raise ValueError("Kỳ phải theo YYYY-MM.")
    if key == "timezone" and value not in ("Asia/Ho_Chi_Minh", "Asia/Saigon"):
        raise ValueError("Bản này dùng múi giờ Asia/Ho_Chi_Minh.")
    if key == "numeric_format" and value not in ("machine", "vi", "en"):
        raise ValueError("Chọn machine, vi hoặc en.")
    if key == "sheets_id":
        from .telegram_sheets import spreadsheet_id
        value = spreadsheet_id(value)
    if key == "sheets_ranges":
        from .telegram_sheets import range_map
        range_map(value)
    if key == "sheets_gid" and not re.fullmatch(r"[0-9]{1,12}", value):
        raise ValueError("gid phải là số không âm đúng từ Google Sheets.")
    if key == "sheets_tab" and (len(value) > 100 or "\n" in value or "!" in value):
        raise ValueError("Tên tab Sheets không hợp lệ.")
    if key == "mapping":
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or len(parsed) > 40 or any(not isinstance(k, str) or not isinstance(v, str) for k, v in parsed.items()):
            raise ValueError("Mapping cần JSON object tên cột đích → tên cột nguồn.")
    return value


class BusinessState:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.root/"business.sqlite3", check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS control(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS memory(key TEXT PRIMARY KEY, value TEXT NOT NULL, actor INTEGER NOT NULL,
            message_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, updated REAL NOT NULL, version INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL,
            actor INTEGER NOT NULL, message_id INTEGER NOT NULL, created REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, state TEXT NOT NULL, digest TEXT,
            telegram_id INTEGER, updated REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
            digest TEXT NOT NULL, size INTEGER NOT NULL, created REAL NOT NULL, actor INTEGER NOT NULL,
            message_id INTEGER NOT NULL, chat_id INTEGER NOT NULL);
        """)
        self.db.commit()
        # Upgrade initial local setup provenance without changing any fact value.
        # SQLite permits encrypted TEXT in these legacy INTEGER-affinity columns.
        migrated = False
        with self.db:
            for table, columns in (("memory", ("actor", "message_id", "chat_id")),
                                   ("events", ("actor", "message_id")), ("sources", ("actor", "message_id", "chat_id"))):
                rows = self.db.execute(f"SELECT rowid AS _rid,{','.join(columns)} FROM {table} WHERE typeof(actor)!='text' OR actor NOT LIKE 'DPAPI1:%'").fetchall()
                for row in rows:
                    self.db.execute(f"UPDATE {table} SET "+",".join(c+"=?" for c in columns)+" WHERE rowid=?",
                                    [*[private_int(row[c]) for c in columns], row["_rid"]])
                    migrated = True
        if migrated:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self):
        with self._lock:
            self.db.close()

    def control(self, key, default=None):
        with self._lock:
            row = self.db.execute("SELECT value FROM control WHERE key=?", (key,)).fetchone()
            if not row:
                return default
            return json.loads(unprotect_text(row[0]) if key in PRIVATE_CONTROL else row[0])

    def set_control(self, key, value):
        # Only structural operational metadata, never arbitrary request content.
        allowed = {"cursor", "paired", "worker_enabled", "schedule_enabled", "approved_plan", "worker_pid", "heartbeat", "last_error", "pending_field", "provider_usage", "provider_cooldown"}
        if key not in allowed:
            raise ValueError("Control key chưa được cho phép.")
        if key == "provider_cooldown" and value is not None:
            if (not isinstance(value, dict) or set(value) != {"status", "until", "failures", "confirmed"}
                    or value["status"] != 429 or not isinstance(value["until"], (int, float)) or isinstance(value["until"], bool)
                    or not math.isfinite(value["until"]) or not 0 <= value["until"] <= 1e12
                    or type(value["failures"]) is not int or not 1 <= value["failures"] <= 5
                    or type(value["confirmed"]) is not bool):
                raise ValueError("Chỉ lưu metadata cooldown 429 đã chuẩn hóa.")
        with self._lock, self.db:
            text = json.dumps(value)
            self.db.execute("INSERT OR REPLACE INTO control VALUES (?, ?)", (key, protect_text(text) if key in PRIVATE_CONTROL else text))

    def remember(self, key, value, actor, message_id, chat_id, confirmation_id=None):
        value = validate_fact(key, value)
        with self._lock, self.db:
            old = self.db.execute("SELECT version FROM memory WHERE key=?", (key,)).fetchone()
            version = old[0]+1 if old else 1
            self.db.execute("INSERT OR REPLACE INTO memory VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, protect_text(value), private_int(actor), private_int(message_id), private_int(chat_id), time.time(), version))
            self.db.execute("INSERT INTO events(kind,key,actor,message_id,created) VALUES ('CONFIRM',?,?,?,?)",
                            (key, private_int(actor), private_int(confirmation_id or message_id), time.time()))
            self.db.execute("INSERT OR REPLACE INTO control VALUES ('schedule_enabled','false')")
            self.db.execute("DELETE FROM control WHERE key='approved_plan'")

    def facts(self):
        with self._lock:
            rows = self.db.execute("SELECT * FROM memory ORDER BY key").fetchall()
            return {r["key"]: {**dict(r), "value": unprotect_text(r["value"]),
                              **{c: read_private_int(r[c]) for c in ("actor", "message_id", "chat_id")},
                              "source_kind": "LOCAL_OWNER_SETUP" if read_private_int(r["message_id"]) == 0 else "TELEGRAM_OWNER_MESSAGE",
                              "verification": "OWNER_PROVIDED_CONFIRMED — chưa đối soát độc lập"} for r in rows}

    def forget(self, key, actor, message_id):
        if key not in FIELDS and key != "all":
            raise ValueError("Chọn trường trong /memory hoặc all.")
        with self._lock, self.db:
            if key == "all":
                self.db.execute("DELETE FROM memory")
                self.db.execute("DELETE FROM events")
            else:
                self.db.execute("DELETE FROM memory WHERE key=?", (key,))
                self.db.execute("DELETE FROM events WHERE key=?", (key,))
            self.db.execute("INSERT INTO events(kind,key,actor,message_id,created) VALUES ('FORGET',?,?,?,?)",
                            (key, private_int(actor), private_int(message_id), time.time()))
            self.db.execute("INSERT OR REPLACE INTO control VALUES ('schedule_enabled','false')")
            self.db.execute("DELETE FROM control WHERE key IN ('approved_plan','pending_field')")
        with self._lock:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def claim(self, identifier):
        with self._lock, self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO deliveries(id,state,updated) VALUES (?,'CLAIMED',?)", (identifier, time.time()))
            return cursor.rowcount == 1

    def delivery(self, identifier, state, text=None, telegram_id=None):
        if state not in ("SENDING", "SENT", "UNKNOWN", "FAILED", "SKIPPED"):
            raise ValueError("Trạng thái giao tin chưa hợp lệ.")
        digest = hashlib.sha256(text.encode()).hexdigest() if text is not None else None
        with self._lock, self.db:
            self.db.execute("UPDATE deliveries SET state=?,digest=COALESCE(?,digest),telegram_id=?,updated=? WHERE id=?",
                            (state, digest, telegram_id, time.time(), identifier))

    def delivery_state(self, identifier):
        with self._lock:
            row = self.db.execute("SELECT state FROM deliveries WHERE id=?", (identifier,)).fetchone()
            return row[0] if row else None

    def source(self, identifier=None):
        with self._lock:
            if identifier:
                row = self.db.execute("SELECT * FROM sources WHERE id=?", (identifier,)).fetchone()
                return {**dict(row), "name": unprotect_text(row["name"]), **{c: read_private_int(row[c]) for c in ("actor", "message_id", "chat_id")}} if row else None
            return [{**dict(r), "name": unprotect_text(r["name"]), **{c: read_private_int(r[c]) for c in ("actor", "message_id", "chat_id")}} for r in self.db.execute("SELECT * FROM sources ORDER BY created DESC LIMIT 20")]

    def add_source(self, name, kind, digest, size, request):
        with self._lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?,?,?,?,?)",
                (digest[:16], protect_text(name), kind, digest, size, time.time(), private_int(request.user_id), private_int(request.message_id), private_int(request.chat_id)))
        return digest[:16]
