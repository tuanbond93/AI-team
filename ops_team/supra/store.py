"""Business-only SQLite event store. Credentials/prompts/AI payloads stay in RAM."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
import json
from contextlib import contextmanager, nullcontext
from .domain import encode, identity, number, Principal


class EventStore:
    SCHEMA_VERSION = 1

    def __init__(self, path=":memory:", private_fragments=()):
        self.private_fragments = list(private_fragments)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.lock = threading.RLock()
        self._in_transaction = False
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, self.SCHEMA_VERSION):
            raise ValueError("Schema SQLite mới hơn code; không tự downgrade dữ liệu.")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots(version TEXT PRIMARY KEY, content TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE NOT NULL,
                entity TEXT NOT NULL, kind TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
                body TEXT NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
            PRAGMA user_version=1;
        """)

    def _guard(self, value):
        text = encode(value)
        if any(secret in text for secret in self.private_fragments if secret and len(secret) >= 8):
            raise ValueError("Nội dung nghiệp vụ trùng bí mật/chỉ dẫn của phiên; đã chặn lưu vào đĩa.")

    @contextmanager
    def transaction(self):
        """Snapshot + audit event commit together, or neither is retained."""
        with self.lock:
            if self._in_transaction:
                raise RuntimeError("Không lồng transaction nghiệp vụ.")
            self.db.execute("SAVEPOINT business_version")
            self._in_transaction = True
            try:
                yield
                self.db.execute("RELEASE SAVEPOINT business_version")
            except Exception:
                self.db.execute("ROLLBACK TO SAVEPOINT business_version")
                self.db.execute("RELEASE SAVEPOINT business_version")
                raise
            finally:
                self._in_transaction = False

    def _commit_context(self):
        return nullcontext() if self._in_transaction else self.db

    def save_version(self, dataset, event_key, kind, actor, body):
        with self.transaction():
            self.save_snapshot(dataset)
            self.append(event_key, dataset["version"], kind, actor, body)

    def save_snapshot(self, dataset):
        # Whitelist normalized business fields; never accept model configuration.
        keys = ("source", "refs", "assumptions", "routes", "fleets", "stores", "actuals", "issues", "pilot_start", "mode", "verification", "customer", "region", "version")
        body = {k: dataset[k] for k in keys}
        for key in ("detail_ledger", "operational_events", "document_source", "document_citations", "legacy_snapshot"):
            if key in dataset:
                body[key] = dataset[key]
        self._guard(body)
        with self.lock, self._commit_context():
            old = self.db.execute("SELECT content FROM snapshots WHERE version=?", (dataset["version"],)).fetchone()
            if old:
                original, incoming = json.loads(old[0]), json.loads(encode(body))
                for item in (original, incoming):
                    item["source"].pop("imported_at", None)
                    item.get("document_source", {}).pop("imported_at", None)
                if original != incoming:
                    raise ValueError("Version nguồn đã tồn tại với nội dung khác; phải tạo version mới.")
            self.db.execute("INSERT OR IGNORE INTO snapshots VALUES (?,?)", (dataset["version"], encode(body)))

    def versions(self, principal):
        principal.require("VIEW_PNL")
        with self.lock:
            return [row[0] for row in self.db.execute("SELECT version FROM snapshots ORDER BY rowid DESC")]

    def load_snapshot(self, principal, version):
        principal.require("VIEW_PNL")
        with self.lock:
            record = self.db.execute("SELECT content FROM snapshots WHERE version=?", (version,)).fetchone()
        if not record:
            raise ValueError("Không có version nguồn.")
        data = json.loads(record[0])
        data["assumptions"] = {k: number(v, optional=True) for k, v in data["assumptions"].items()}
        for row in data["routes"]:
            for key in ("stores", "kg"):
                row[key] = number(row[key])
        for row in data["fleets"]:
            for key in ("vehicles", "legal_kg", "trips", "kg"):
                row[key] = number(row[key], optional=True)
        for row in data["stores"]:
            row["kg"] = number(row["kg"], optional=True)
        return data

    def append(self, event_key, entity, kind, actor, body):
        self._guard([event_key, entity, kind, actor, body])
        with self.lock, self._commit_context():
            existing = self.db.execute("SELECT event_hash FROM events WHERE event_key=?", (event_key,)).fetchone()
            if existing:
                return False
            previous = self.db.execute("SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            previous = previous[0] if previous else "GENESIS"
            stamp = datetime.now(timezone.utc).isoformat()
            digest = identity([event_key, entity, kind, actor, stamp, body, previous])
            self.db.execute("INSERT INTO events(event_key,entity,kind,actor,created_at,body,previous_hash,event_hash) VALUES (?,?,?,?,?,?,?,?)",
                (event_key, entity, kind, actor, stamp, encode(body), previous, digest))
            return True

    def events(self, entity=None):
        with self.lock:
            self.db.row_factory = sqlite3.Row
            query = "SELECT * FROM events"+(" WHERE entity=?" if entity else "")+" ORDER BY seq"
            return [{**dict(row), "body": json.loads(row["body"])} for row in self.db.execute(query, (entity,) if entity else ())]

    def verify_chain(self):
        previous = "GENESIS"
        for event in self.events():
            if event["previous_hash"] != previous or identity([event["event_key"], event["entity"], event["kind"], event["actor"], event["created_at"], event["body"], previous]) != event["event_hash"]:
                return False
            previous = event["event_hash"]
        return True

    def close(self):
        self.db.close()
