"""Opt-in Telegram polling, pairing and routing. Secrets and chats stay in RAM."""
from dataclasses import dataclass, field
import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
import weakref
import httpx


class TelegramError(RuntimeError):
    def __init__(self, message, *, transient=False, retry_after=None):
        super().__init__(message)
        self.transient, self.retry_after = transient, retry_after


class _TelegramURLFilter(logging.Filter):
    """Bot API credentials live in the URL; redact optional HTTP diagnostics too."""
    def filter(self, record):
        rendered = record.getMessage()
        clean = re.sub(r"(/bot)[0-9]{5,16}:[A-Za-z0-9_-]{20,100}", r"\1[REDACTED]", rendered)
        clean = re.sub(r"https://sheets\.googleapis\.com/\S+", "[Google Sheets readonly endpoint]", clean)
        if clean != rendered:
            record.msg, record.args = clean, ()
        return True


for _logger_name in ("httpx", "httpcore.http11", "httpcore.http2", "httpcore.connection", "httpcore.proxy"):
    logging.getLogger(_logger_name).addFilter(_TelegramURLFilter())


class TelegramAPI:
    def __init__(self, token, client=None):
        if not re.fullmatch(r"[0-9]{5,16}:[A-Za-z0-9_-]{20,100}", token):
            raise TelegramError("Token bot chưa đúng định dạng. Lấy token từ BotFather.")
        self._token = token
        self._client = client or httpx.Client(trust_env=False, follow_redirects=False)

    def call(self, method, **params):
        if method not in {"getMe", "getWebhookInfo", "getUpdates", "sendMessage", "getFile"}:
            raise TelegramError("Phương thức Telegram chưa được cho phép.")
        try:
            with self._client.stream("POST", f"https://api.telegram.org/bot{self._token}/{method}",
                                     json=params, timeout=20) as response:
                if response.status_code != 200:
                    hints = {401: "Token bot không hợp lệ.", 403: "Bot chưa có quyền gửi vào nhóm.",
                             409: "Bot đang dùng webhook hoặc có worker khác đang nhận tin.",
                             429: "Telegram đang giới hạn tốc độ; dừng và thử lại sau."}
                    try:
                        wait = float(response.headers.get("retry-after", "10"))
                    except ValueError:
                        wait = 10
                    wait = wait if 0 <= wait <= 60 else 10
                    raise TelegramError(hints.get(response.status_code, "Telegram chưa sẵn sàng. Thử lại sau."),
                        transient=response.status_code in (408, 429) or response.status_code >= 500,
                        retry_after=wait if response.status_code == 429 or response.headers.get("retry-after") else None)
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 512_000:
                        raise TelegramError("Phản hồi Telegram vượt giới hạn.")
                    chunks.append(chunk)
            import json
            data = json.loads(b"".join(chunks))
            if not isinstance(data, dict) or data.get("ok") is not True or "result" not in data:
                raise TelegramError("Telegram trả kết quả không hợp lệ.")
            return data["result"]
        except TelegramError:
            raise
        except Exception:
            # Exception URLs and Telegram error descriptions can contain tokens.
            raise TelegramError("Không kết nối được Telegram. Kiểm tra mạng rồi thử lại.", transient=True) from None

    def close(self):
        self._client.close()

    def download(self, file_path):
        if not isinstance(file_path, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,240}", file_path) or ".." in file_path or file_path.startswith("/"):
            raise TelegramError("Đường dẫn file Telegram không hợp lệ.")
        try:
            with self._client.stream("GET", f"https://api.telegram.org/file/bot{self._token}/{file_path}", timeout=30) as response:
                if response.status_code != 200:
                    raise TelegramError("Không tải được file Telegram.")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 20*1024*1024:
                        raise TelegramError("File vượt 20 MB.")
                    chunks.append(chunk)
                return b"".join(chunks)
        except TelegramError:
            raise
        except Exception:
            raise TelegramError("Lỗi tải file Telegram; không ghi URL/token ra log.") from None


@dataclass(frozen=True)
class Incoming:
    chat_id: int
    user_id: int
    message_id: int
    topic_id: int | None
    command: str
    text: str = field(repr=False)


def parse_message(message, username, bot_id):
    """Only explicit commands, mentions or replies; ignore anonymous/forwarded input."""
    if not isinstance(message, dict):
        return None
    sender, chat = message.get("from"), message.get("chat")
    if not isinstance(sender, dict) or not isinstance(chat, dict):
        return None
    if (chat.get("type") not in ("group", "supergroup") or not isinstance(chat.get("id"), int)
            or chat["id"] >= 0 or not isinstance(sender.get("id"), int) or sender.get("is_bot") is not False
            or message.get("sender_chat") or any(k in message for k in ("forward_origin", "forward_from", "forward_from_chat"))):
        return None
    text, message_id = message.get("text"), message.get("message_id")
    if not isinstance(text, str) or not isinstance(message_id, int) or not text.strip():
        return None
    text = text.strip()
    command = None
    if text.startswith("/"):
        first, _, text = text.partition(" ")
        name, _, target = first[1:].partition("@")
        if target and target.casefold() != username.casefold():
            return None
        if not re.fullmatch("[a-z_]{1,32}", name):
            return None
        command = name
    else:
        # Match Telegram's mention entity so foo@bot in an address is not a summons.
        mention = "@"+username
        # Entity offsets reference the original, untrimmed text.
        units = message["text"].encode("utf-16-le")
        mentions = [(e.get("offset"), e.get("length")) for e in (message.get("entities") or [])
                    if isinstance(e, dict) and e.get("type") == "mention"]
        addressed = any(isinstance(o, int) and isinstance(n, int) and
                        units[o*2:(o+n)*2].decode("utf-16-le", errors="replace").casefold() == mention.casefold()
                        for o, n in mentions)
        reply_message = message.get("reply_to_message")
        reply = reply_message.get("from", {}) if isinstance(reply_message, dict) else {}
        if not isinstance(reply, dict):
            reply = {}
        if not addressed and not (reply.get("is_bot") is True and reply.get("id") == bot_id):
            return None
        if addressed:
            text = re.sub(re.escape(mention)+r"\b", "", text, flags=re.I).strip()
        command = "ask"
    return Incoming(chat["id"], sender["id"], message_id, message.get("message_thread_id"), command, text.strip())


def chunks_for_telegram(text, limit=3500, *, continuation=False):
    """Telegram's text length is counted in UTF-16 units, including astral emoji."""
    part, size = [], 0
    prefix = "Phần bổ sung (tiếp):\n"
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if size+width > limit:
            yield "".join(part)
            part, size = (list(prefix), len(prefix)) if continuation else ([], 0)
        part.append(char)
        size += width
    if part:
        yield "".join(part)


_leases = {}
_lease_lock = threading.Lock()


class TelegramBridge:
    """One bounded, sequential worker; no file, webhook, operation or scheduler APIs."""
    def __init__(self, api, responder, extra_users=(), *, clock=time.time, lifetime=7200,
                 expected_chat=-5395485115, owner_id=None, state=None):
        self._api, self._responder, self._clock = api, responder, clock
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._thread = None
        self._lease = hashlib.sha256(api._token.encode()).hexdigest()
        self._pair_code = secrets.token_urlsafe(9)
        self._started = int(clock())
        self._expires = self._started+lifetime if lifetime is not None else float("inf")
        self._pair_expires = self._started+600
        self._state_store = state
        self._expected_chat, self._owner_id = expected_chat, owner_id
        self._offset = state.control("cursor", 0) if state else 0
        self._chat = None
        self._users = set(extra_users)
        self._username, self._bot_id = "", None
        self._received, self._answered = 0, 0
        self._state, self._error = "Chưa kết nối", ""
        paired = state.control("paired") if state else None
        if paired == {"chat_id": expected_chat, "owner_id": owner_id} and owner_id:
            self._chat = expected_chat
            self._users.add(owner_id)

    @property
    def pairing_command(self):
        return f"/pair@{self._username} {self._pair_code}"

    def snapshot(self):
        with self._lock:
            return dict(state=self._state, error=self._error, username=self._username,
                        chat_id=self._chat, user_ids=sorted(self._users), received=self._received,
                        answered=self._answered, alive=bool(self._thread and self._thread.is_alive()))

    def start(self):
        with _lease_lock:
            existing = _leases.get(self._lease)
            active = existing() if existing else None
            if active is not None and active.snapshot()["alive"]:
                raise TelegramError("Bot này đã có worker trong ứng dụng. Dừng worker cũ trước.")
            # Read-only preflight; never delete another integration's webhook.
            me = self._api.call("getMe")
            if (not isinstance(me, dict) or me.get("is_bot") is not True or
                    not re.fullmatch(r"[A-Za-z0-9_]{5,32}", str(me.get("username", ""))) or not isinstance(me.get("id"), int)):
                raise TelegramError("Telegram không trả danh tính bot hợp lệ.")
            webhook = self._api.call("getWebhookInfo")
            if not isinstance(webhook, dict) or webhook.get("url"):
                raise TelegramError("Bot đang có webhook. Hãy dùng bot thử nghiệm riêng; không xóa webhook hiện có.")
            self._username, self._bot_id = me["username"], me["id"]
            self._responder.cancel = self._stop
            self._state = "Đang chạy" if self._chat is not None else "Chờ xác minh owner / ghép nối"
            if self._state_store:
                from .telegram_secrets import save_protected
                import json
                save_protected(self._state_store.root/"pair.dpapi", json.dumps({"command": self.pairing_command,
                    "expires": self._pair_expires, "owner_id": self._owner_id, "chat_id": self._expected_chat}).encode())
            self._thread = threading.Thread(target=self._run, name="ai-team-telegram", daemon=True)
            _leases[self._lease] = weakref.ref(self)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._state_store:
            self._state_store.set_control("worker_enabled", False)
        with self._lock:
            self._state = "Đang dừng" if self.snapshot()["alive"] else "Đã dừng"

    def _send(self, request, text, delivery_id=None):
        # Never repeat sendMessage after an ambiguous transport failure.
        parts = list(chunks_for_telegram(text, continuation=True))
        for index, part in enumerate(parts):
            if (self._stop.is_set() or self._clock() >= self._expires or
                    (self._state_store and not self._state_store.control("worker_enabled", False))):
                return False
            identifier = f"{delivery_id}:part:{index}" if delivery_id else None
            if self._state_store and identifier:
                if not self._state_store.claim(identifier):
                    if self._state_store.delivery_state(identifier) == "SENT":
                        continue
                    return False
                self._state_store.delivery(identifier, "SENDING", part)
            params = dict(chat_id=request.chat_id, text=part, link_preview_options={"is_disabled": True})
            if request.message_id > 0:
                params["reply_parameters"] = {"message_id": request.message_id, "allow_sending_without_reply": True}
            if request.topic_id is not None:
                params["message_thread_id"] = request.topic_id
            try:
                result = self._api.call("sendMessage", **params)
                if self._state_store and identifier:
                    self._state_store.delivery(identifier, "SENT", telegram_id=result.get("message_id") if isinstance(result, dict) else None)
            except Exception:
                if self._state_store and identifier:
                    self._state_store.delivery(identifier, "UNKNOWN")
                raise
            if self._stop.wait(1):
                return index == len(parts)-1
        return True

    def _backoff(self, delay):
        remaining = min(60, max(0.1, delay))
        while remaining > 0:
            if self._stop.is_set() or (self._state_store and not self._state_store.control("worker_enabled", False)):
                self._stop.set()
                return True
            step = min(1, remaining)
            if self._stop.wait(step):
                return True
            remaining -= step
        return False

    def process(self, update):
        """Update ID is consumed before work; duplicates cannot spend API budget twice."""
        if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
            return
        if update["update_id"] < self._offset:
            return
        self._offset = update["update_id"]+1
        if self._state_store:
            self._state_store.set_control("cursor", self._offset)
        message = update.get("message", {})
        minimum_date = self._started-3600 if self._state_store and self._chat is not None else self._started
        if not isinstance(message, dict) or not isinstance(message.get("date"), (int, float)) or message["date"] < minimum_date:
            return
        document = message.get("document")
        parsed_message = {**message, "text": message.get("caption", "/import")} if isinstance(document, dict) else message
        request = parse_message(parsed_message, self._username, self._bot_id)
        if (request is None or self._stop.is_set() or self._clock() >= self._expires or
                (self._state_store and not self._state_store.control("worker_enabled", False))):
            return
        if self._chat is None:
            if request.chat_id != self._expected_chat:
                return
            if request.command == "id":
                self._send(request, f"User ID của bạn: {request.user_id}. Chủ máy cần nhập ID này vào cấu hình local, rồi xác minh bằng mã ghép nối.", f"id:{update['update_id']}")
                return
            if (not self._owner_id or request.user_id != self._owner_id or request.command != "pair" or
                    self._clock() > self._pair_expires or not hmac.compare_digest(request.text, self._pair_code)):
                return
            with self._lock:
                self._chat = request.chat_id
                self._users.add(self._owner_id)
                self._state = "Đang chạy"
            if self._state_store:
                self._state_store.set_control("paired", {"chat_id": self._expected_chat, "owner_id": self._owner_id})
            self._send(request, "Đã xác minh đúng owner và nhóm AI Team. Gõ /onboard@"+self._username+" để bắt đầu; /help để xem lệnh.", f"pair:{update['update_id']}")
            return
        if request.chat_id != self._chat or request.user_id not in self._users:
            return
        with self._lock:
            self._received += 1
        job = f"update:{update['update_id']}"
        if self._state_store and not self._state_store.claim(job):
            return
        try:
            if not self._responder.safe_to_send(request.text, extra_secrets=(self._api._token,)):
                answer = "Nội dung trùng khóa hoặc chỉ dẫn riêng; đã chặn gửi tới model."
            elif isinstance(document, dict):
                if request.command != "import" or not hasattr(self._responder, "document"):
                    answer = "Gửi file kèm caption /import hoặc /import costs."
                else:
                    answer = self._responder.document(request, document, self._api)
            else:
                answer = self._responder.answer(request)
        except Exception:
            answer = "Không hoàn tất xử lý. Kiểm tra trạng thái trong AI Team; yêu cầu này chưa được xác nhận hoàn thành."
        # Last boundary also protects deterministic help/error/source content.
        if not self._responder.safe_to_send(answer, extra_secrets=(self._api._token,)):
            answer = "Đã chặn phản hồi chứa nội dung riêng của phiên."
        try:
            delivered = self._send(request, answer, job)
        except TelegramError:
            if self._state_store:
                self._state_store.delivery(job, "UNKNOWN")
            raise
        if self._state_store:
            self._state_store.delivery(job, "SENT" if delivered else "SKIPPED")
        with self._lock:
            self._answered += int(delivered)

    def _run(self):
        try:
            failures = 0
            while not self._stop.is_set() and self._clock() < self._expires:
                if self._state_store:
                    self._state_store.set_control("heartbeat", self._clock())
                    if not self._state_store.control("worker_enabled", False):
                        break
                try:
                    updates = self._api.call("getUpdates", offset=self._offset, timeout=10,
                                             limit=20, allowed_updates=["message"])
                    failures = 0
                    if self._state_store:
                        self._state_store.set_control("last_error", "")
                    with self._lock:
                        self._state = "Đang chạy" if self._chat is not None else "Chờ xác minh owner / ghép nối"
                except TelegramError as exc:
                    if not exc.transient:
                        raise
                    failures += 1
                    delay = exc.retry_after if exc.retry_after is not None else min(60, 2**min(failures, 6))
                    with self._lock:
                        self._state = "Đang kết nối lại"
                    if self._state_store:
                        self._state_store.set_control("last_error", "Telegram tạm gián đoạn; worker đang kết nối lại với backoff tối đa 60 giây. Có thể dừng tại app.")
                    if self._backoff(delay):
                        break
                    continue
                if not isinstance(updates, list):
                    raise TelegramError("Telegram trả danh sách tin nhắn không hợp lệ.")
                for update in updates:
                    try:
                        self.process(update)
                    except TelegramError as exc:
                        if not exc.transient:
                            raise
                        if self._state_store:
                            self._state_store.set_control("last_error", "Một tin gửi có kết quả chưa rõ; đã giữ UNKNOWN, không tự gửi lại. Worker tiếp tục nhận tin.")
                if self._chat is not None and not self._stop.is_set() and hasattr(self._responder, "tick"):
                    try:
                        self._responder.tick(self._send)
                    except TelegramError as exc:
                        if not exc.transient:
                            raise
                        if self._state_store:
                            self._state_store.set_control("last_error", "Báo cáo có kết quả gửi chưa rõ; giữ UNKNOWN và không tự gửi lại. Worker vẫn hoạt động.")
        except TelegramError as exc:
            with self._lock:
                self._error = str(exc)
        except Exception:
            with self._lock:
                self._error = "Worker dừng do lỗi xử lý; không ghi nội dung hoặc token ra log."
        finally:
            self._stop.set()
            self._responder.clear()
            self._api.close()
            self._api._token = ""
            with self._lock:
                self._state = "Đã dừng"
            if self._state_store:
                self._state_store.set_control("last_error", self._error)
                self._state_store.set_control("worker_enabled", False)
            with _lease_lock:
                if _leases.get(self._lease, lambda: None)() is self:
                    _leases.pop(self._lease, None)
