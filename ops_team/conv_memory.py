"""Conversation memory per user, inside the existing vault database.

Kinds: preference (xưng hô, độ dài), semantic (điều người dùng bảo nhớ),
episodic (kết quả công việc do code tạo, có ngày), project (ghi chú dự án do
owner bảo nhớ). Working memory lives in RAM in ``conversation.py``.

Rules (ideas from Letta/Honcho/mem0/Graphiti, kept local and small):
- Only explicit user statements or code results are stored; nothing inferred.
- Scope = chat + user: one user never reads another user's memory.
- Values are DPAPI-encrypted like every other vault value.
- A changed fact supersedes the old one (valid_to set), so history keeps time
  and source instead of silently overwriting.
- Memory text is data for wording only. It never grants permissions and never
  becomes a price, rule or exception.
"""
import hashlib
import re
import time

from .intent import _plain as plain
from .telegram_state import contains_secret, protect_text, unprotect_text

KINDS = ('preference', 'semantic', 'episodic', 'project')
MAX_PER_SCOPE = {'preference': 10, 'semantic': 40, 'episodic': 40, 'project': 40}
EPISODE_DAYS = 45

# Not stored without a proper permission flow (CLAUDE.md: no secrets, no private data).
SENSITIVE = [
    (r'mat khau|password|\bpass\b|\botp\b|ma pin|\bpin\b|api key|token|khoa bi mat|private key|cookie', 'mật khẩu/khóa'),
    (r'so tai khoan|\bstk\b|the tin dung|\bcvv\b|ngan hang', 'tài khoản ngân hàng'),
    (r'cccd|cmnd|can cuoc|ho chieu|passport|\bmst\b ca nhan', 'giấy tờ tùy thân'),
    (r'benh|suc khoe|thuoc|mang thai|ton giao|chinh tri|xu huong tinh duc', 'thông tin nhạy cảm cá nhân'),
]
DIGITS = re.compile(r'(?<!\d)(?:\+?84|0)\d{8,10}(?!\d)|(?<!\d)\d{9,19}(?!\d)')
# Business rules must come through approved commands/tables, never chat memory.
BUSINESS_RULE = r'\b(gia|don gia|bang gia|phi|cuoc|quy tac|cong thuc|ngoai le|chinh sach|vat|thue|luong|dinh muc)\b'
OVERRIDE = r'bo qua (moi|tat ca|cac)? ?(quy tac|lenh|chi dan|huong dan)|ignore (all|previous)|system prompt|vuot quyen|cap quyen|lam owner|xac nhan thay'


def scope_of(chat_id, user_id):
    return hashlib.sha256(f'conv:{chat_id}:{user_id}'.encode()).hexdigest()[:32]


def check_storable(text):
    """None if the text may be stored, else a short reason in Vietnamese."""
    t = plain(text)
    if contains_secret(text) or DIGITS.search(text):
        return 'có số/khóa có thể là thông tin riêng'
    for pattern, label in SENSITIVE:
        if re.search(pattern, t):
            return label
    if re.search(OVERRIDE, t):
        return 'nội dung giống chỉ thị vượt quyền'
    return None


def is_business_rule(text):
    return bool(re.search(BUSINESS_RULE, plain(text)))


class ConversationMemory:
    def __init__(self, state, clock=time.time):
        self.state, self.clock = state, clock
        with state._lock, state.db:
            state.db.executescript("""
              CREATE TABLE IF NOT EXISTS conv_memory(id INTEGER PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL,
                key TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL, created REAL NOT NULL,
                valid_to REAL, superseded_by INTEGER);
              CREATE INDEX IF NOT EXISTS conv_memory_scope ON conv_memory(scope, valid_to);
            """)

    def _rows(self, scope, kinds=KINDS, include_old=False):
        sql = "SELECT * FROM conv_memory WHERE scope=? AND kind IN (%s)" % ','.join('?'*len(kinds))
        if not include_old:
            sql += " AND valid_to IS NULL"
        with self.state._lock:
            rows = self.state.db.execute(sql+" ORDER BY id", (scope, *kinds)).fetchall()
        return [{**dict(r), 'value': unprotect_text(r['value']), 'source': unprotect_text(r['source'])} for r in rows]

    def add(self, scope, kind, value, source, key=None):
        """Store an explicit memory. Same key supersedes the previous value. Returns the row id."""
        if kind not in KINDS:
            raise ValueError('Loại trí nhớ không hợp lệ.')
        value = ' '.join(str(value).split())[:400]
        if not value:
            raise ValueError('Nội dung trống.')
        if kind != 'episodic' and (reason := check_storable(value)):
            raise ValueError(reason)
        key = key or 'k:'+hashlib.sha256(plain(value).encode()).hexdigest()[:16]
        now = self.clock()
        with self.state._lock, self.state.db:
            db = self.state.db
            cursor = db.execute("INSERT INTO conv_memory(scope,kind,key,value,source,created) VALUES (?,?,?,?,?,?)",
                                (scope, kind, key, protect_text(value), protect_text(source), now))
            new_id = cursor.lastrowid
            db.execute("UPDATE conv_memory SET valid_to=?, superseded_by=? WHERE scope=? AND kind=? AND key=? AND id!=? AND valid_to IS NULL",
                       (now, new_id, scope, kind, key, new_id))
            # Bound each kind: oldest current rows expire first; episodes also age out.
            current = [r[0] for r in db.execute("SELECT id FROM conv_memory WHERE scope=? AND kind=? AND valid_to IS NULL ORDER BY id DESC",
                                                 (scope, kind)).fetchall()]
            for old in current[MAX_PER_SCOPE[kind]:]:
                db.execute("DELETE FROM conv_memory WHERE id=?", (old,))
            db.execute("DELETE FROM conv_memory WHERE scope=? AND kind='episodic' AND created<?", (scope, now-EPISODE_DAYS*86400))
        return new_id

    def preferences(self, scope):
        return {r['key']: r['value'] for r in self._rows(scope, ('preference',))}

    def listing(self, scope):
        return [r for r in self._rows(scope) if r['kind'] != 'episodic'] + self._rows(scope, ('episodic',))[-5:]

    def relevant(self, scope, text, limit=3, kinds=('semantic', 'project', 'episodic')):
        """Rows sharing words with the message; nothing when nothing overlaps."""
        words = {w for w in plain(text).split() if len(w) > 2} - STOP
        scored = []
        for row in self._rows(scope, kinds):
            overlap = words & (set(plain(row['value']).split()) - STOP)
            if overlap:
                scored.append((len(overlap), row['id'], row))
        return [r for _, _, r in sorted(scored, key=lambda x: (-x[0], -x[1]))[:limit]]

    def episodes(self, scope, since=None):
        rows = self._rows(scope, ('episodic',))
        return [r for r in rows if since is None or r['created'] >= since]

    def forget(self, scope, row_id=None):
        """Delete one row (by id) or everything in this scope. Returns rows removed."""
        with self.state._lock, self.state.db:
            if row_id is None:
                count = self.state.db.execute("DELETE FROM conv_memory WHERE scope=?", (scope,)).rowcount
            else:
                count = self.state.db.execute("DELETE FROM conv_memory WHERE scope=? AND id=?", (scope, row_id)).rowcount
        with self.state._lock:
            self.state.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return count


STOP = {'anh', 'chi', 'em', 'minh', 'ban', 'toi', 'cua', 'cho', 'nay', 'kia', 'roi', 'khong', 'nhe', 'nha', 'voi', 'gi',
        'the', 'nao', 'cai', 'viec', 'duoc', 'lam', 'hay', 'nho', 'can', 'con', 'vay', 'thi', 'mot', 'nhung', 'cac', 'hom',
        'truoc', 'noi', 'bao', 'lan', 'hoi', 'xem', 'gui', 'giup', 'dang', 'den', 'tu', 'tren', 'duoi', 'qua'}
