"""Volatile request accounting. No payloads or credentials are retained here."""
from dataclasses import dataclass, field
import time
import math


class ProviderError(RuntimeError):
    def __init__(self, message, *, status_code=None, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def retry_seconds(value):
    """Only bounded finite seconds; no provider body or arbitrary headers retained."""
    try:
        seconds = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return seconds if math.isfinite(seconds) and 0 <= seconds <= 86400 else None


@dataclass
class UsageWindow:
    events: list = field(default_factory=list)


@dataclass
class RequestBudget:
    max_calls: int = 24
    max_tokens: int = 60000
    tokens_per_minute: int = 7500
    window: UsageWindow = field(default_factory=UsageWindow)
    clock: object = time.monotonic
    sleep: object = time.sleep
    attempts: int = 0
    reserved_tokens: int = 0
    halted: bool = False

    def acquire(self, tokens, deadline):
        if self.halted:
            raise ProviderError("Đã dừng API trong lượt này sau lỗi quyền/quota; sửa cấu hình rồi chạy lượt mới.")
        if self.attempts >= self.max_calls or self.reserved_tokens + tokens > self.max_tokens:
            raise ProviderError("Hết ngân sách call/token của lượt; kết quả còn mở, không tự đổi provider/model.")
        if tokens > self.tokens_per_minute:
            raise ProviderError("Yêu cầu vượt ngân sách token/phút ước tính; thu hẹp kỳ/kho/câu hỏi hoặc chỉ dẫn. Không cắt ngầm bằng chứng.")
        while True:
            now = self.clock()
            self.window.events[:] = [(t, n) for t, n in self.window.events if now-t < 60]
            if sum(n for _, n in self.window.events) + tokens <= self.tokens_per_minute:
                break
            wait = max(0.01, 60-(now-self.window.events[0][0])+0.01)
            if now+wait+1 >= deadline:
                raise ProviderError("Đạt giới hạn token/phút ước tính; không đủ thời gian chờ trong lượt. Chờ ít nhất 60 giây rồi chạy lại; kiểm toán còn mở.")
            self.sleep(wait)
        if self.clock() >= deadline:
            raise ProviderError("Hết thời gian trước khi gửi API.")
        self.attempts += 1
        self.reserved_tokens += tokens
        self.window.events.append((self.clock(), tokens))


class TelegramBudget(RequestBudget):
    """Same caps/counters, with explicit user-turn recovery for terminal HTTP 429."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__post_init__()

    def __post_init__(self):
        self.halt_status = None
        self.retry_until = None
        self.retry_failures = 0
        self.retry_confirmed = False

    def provider_failed(self, status, retry_after=None):
        self.halted, self.halt_status = True, status
        self.retry_until = None
        if status == 429:
            self.retry_failures = min(5, self.retry_failures+1)
            seconds = retry_seconds(retry_after)
            self.retry_confirmed = seconds is not None
            pause = max(1, seconds) if seconds is not None else min(900, 60*2**min(self.retry_failures-1, 4))
            self.retry_until = self.clock()+pause
        self.persist_cooldown()

    def provider_succeeded(self):
        self.retry_failures = 0

    def begin_turn(self):
        # Never called by a retry loop/probe. Auth failures do not become cooldowns.
        if self.halted and self.halt_status == 429 and self.retry_until is not None and self.clock() >= self.retry_until:
            self.halted = False
            self.halt_status, self.retry_until = None, None
            self.persist_cooldown()

    def persist_cooldown(self):
        pass  # Durable worker specializes only sanitized numerical metadata.

    def acquire(self, tokens, deadline):
        if self.halted and self.halt_status == 429:
            remaining = max(0, math.ceil((self.retry_until or self.clock())-self.clock()))
            raise ProviderError(f"API đang tạm ngưng sau HTTP 429; còn khoảng {remaining} giây trước lần thử do người dùng yêu cầu. Thời điểm này không xác nhận quota provider đã phục hồi.", status_code=429, retry_after=remaining)
        if self.halted and self.halt_status in (401, 403):
            raise ProviderError("Đã dừng API do khóa/quyền model; chủ máy cần sửa cấu hình trước lượt mới.", status_code=self.halt_status)
        super().acquire(tokens, deadline)


def estimate_tokens(body):
    # Deliberately conservative heuristic for Vietnamese/JSON, not a tokenizer
    # or a provider-wide guarantee. Include schema, messages and output reserve.
    import json
    size = len(json.dumps({k: v for k, v in body.items() if k not in ("max_tokens", "max_completion_tokens")}, ensure_ascii=False).encode("utf-8"))
    return math.ceil(size/3) + 64 + body.get("max_tokens", body.get("max_completion_tokens", 0))
