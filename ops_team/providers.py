"""API transport. Call configuration and request bodies live only in RAM."""
from dataclasses import dataclass, field
import json
import time
import asyncio
import httpx
from .limits import ProviderError, RequestBudget, estimate_tokens, retry_seconds

ENDPOINTS = {
    "Groq": "https://api.groq.com/openai/v1/chat/completions",
    "OpenAI": "https://api.openai.com/v1/chat/completions",
    "Gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "Ollama (local)": "http://127.0.0.1:11434/v1/chat/completions",
}


FREE_MODELS = ("qwen/qwen3.8-27b", "openai/gpt-oss-20b", "openai/gpt-oss-120b")


@dataclass(repr=False)
class ModelConfig:
    provider: str
    model: str
    api_key: str = field(default="", repr=False)
    instructions: str = field(default="", repr=False)
    structured_output: str = "json_schema"
    max_output_tokens: int = 3000
    free_pilot: bool = False
    free_account_confirmed: bool = False


def validate_free_config(config):
    if (not config.free_pilot or config.provider != "Groq" or config.model not in FREE_MODELS
            or config.structured_output != "json_schema"):
        raise ProviderError("FREE PILOT chỉ cho Groq + model trong danh sách Free Plan + JSON Schema strict; không gọi provider/model khác.")
    if not config.free_account_confirmed:
        raise ProviderError("Cần xác nhận tài khoản Groq đang dùng Free Plan, không bật billing. App không tự xác minh gói/phí tài khoản.")


def contract():
    return {
        "summary": "string",
        "observations": [{"text": "string", "evidence_ids": ["metric id"]}],
        "hypotheses": ["string"],
        "proposals": ["string"],
        "requests": [{"to": "agent name", "question": "string", "evidence_ids": ["metric id"]}],
        "disagreements": ["string"],
        "selected_agents": ["specialist name; Coordinator plan only"],
        "audit_decisions": [{"task_id": "exact version task_id from review_tasks", "verdict": "accept/revise/unresolved",
                             "reason": "string", "evidence_ids": ["metric id"]}],
    }


def output_schema():
    text = {"type": "string"}
    strings = {"type": "array", "items": text}
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    observation = obj({"text": text, "evidence_ids": strings})
    request = obj({"to": text, "question": text, "evidence_ids": strings})
    decision = obj({"task_id": text, "verdict": {"type": "string", "enum": ["accept", "revise", "unresolved"]}, "reason": text, "evidence_ids": strings})
    return obj({"summary": text, "observations": {"type": "array", "items": observation},
                "hypotheses": strings, "proposals": strings, "requests": {"type": "array", "items": request},
                "disagreements": strings, "selected_agents": strings, "audit_decisions": {"type": "array", "items": decision}})


def validate_output(data, allowed_ids):
    if not isinstance(data, dict):
        raise ProviderError("Đầu ra không phải JSON object.")
    for field_name in ("summary",):
        if not isinstance(data.get(field_name), str) or len(data[field_name]) > 10000:
            raise ProviderError("Đầu ra thiếu summary hợp lệ.")
    for field_name in ("observations", "hypotheses", "proposals", "requests", "disagreements", "selected_agents"):
        if not isinstance(data.get(field_name), list) or len(data[field_name]) > 20:
            raise ProviderError("Đầu ra sai cấu trúc: " + field_name)
    for field_name in ("hypotheses", "proposals", "disagreements", "selected_agents"):
        if not all(isinstance(v, str) and len(v) <= 4000 for v in data[field_name]):
            raise ProviderError("Đầu ra chứa mục không hợp lệ: " + field_name)
    for item in [*data["observations"], *data["requests"]]:
        if not isinstance(item, dict) or not isinstance(item.get("evidence_ids"), list):
            raise ProviderError("Đầu ra thiếu evidence_ids.")
        if not all(isinstance(v, str) and v in allowed_ids for v in item["evidence_ids"]):
            raise ProviderError("Đầu ra dẫn mã bằng chứng không tồn tại.")
    for item in data["observations"]:
        if not isinstance(item.get("text"), str) or len(item["text"]) > 4000:
            raise ProviderError("Observation không hợp lệ.")
    for item in data["requests"]:
        if not isinstance(item.get("to"), str) or not isinstance(item.get("question"), str) or len(item["question"]) > 4000:
            raise ProviderError("Yêu cầu bổ sung không hợp lệ.")
    decisions = data.get("audit_decisions", [])
    if not isinstance(decisions, list) or len(decisions) > 60:
        raise ProviderError("Quyết định Auditor sai cấu trúc.")
    for d in decisions:
        if not isinstance(d, dict) or d.get("verdict") not in ("accept", "revise", "unresolved"):
            raise ProviderError("Verdict Auditor không hợp lệ.")
        if not all(isinstance(d.get(k), str) and 0 < len(d[k]) <= 4000 for k in ("task_id", "reason")):
            raise ProviderError("Quyết định Auditor thiếu phiên bản/lý do.")
        if not isinstance(d.get("evidence_ids"), list) or any(not isinstance(i, str) or i not in allowed_ids for i in d["evidence_ids"]):
            raise ProviderError("Quyết định Auditor dẫn nguồn không hợp lệ.")
    return {**{k: data[k] for k in contract() if k != "audit_decisions"}, "audit_decisions": decisions}


async def _fetch_async(url, headers, body, timeout):
    # Cancel the local connection as one wall-clock operation, without leaving
    # a worker thread waiting. A remote provider may still bill its processing.
    async def send():
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as connection:
            async with connection.stream("POST", url, headers=headers, json=body, timeout=timeout) as response:
                if response.status_code != 200:
                    return response.status_code, b"", response.headers.get("retry-after")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 256_000:
                        raise ProviderError("Phản hồi API vượt giới hạn 256 KB.")
                    chunks.append(chunk)
                return response.status_code, b"".join(chunks), None
    return await asyncio.wait_for(send(), timeout=timeout)


def call_model(config, payload, timeout=30, client=None, budget=None, deadline=None, *, response_kind="analysis"):
    # The caller chooses the contract explicitly; payload fields cannot switch
    # business responses to the less demanding connection probe validator.
    if response_kind not in ("analysis", "probe"):
        raise ProviderError("Loại phản hồi model không hợp lệ.")
    schema = ({"type": "object", "properties": {"status": {"type": "string", "enum": ["ok"]}},
               "required": ["status"], "additionalProperties": False}
              if response_kind == "probe" else output_schema())
    if config.free_pilot:
        validate_free_config(config)
        if budget is None:
            raise ProviderError("FREE PILOT cần ngân sách call/token trước mạng.")
    if config.provider not in ENDPOINTS or not config.model.strip():
        raise ProviderError("Chưa cấu hình provider/model.")
    if config.provider != "Ollama (local)" and not config.api_key.strip():
        raise ProviderError("Chưa có khóa API cho agent.")
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = "Bearer " + config.api_key
    content = {**payload, "output_format": "JSON"}
    if config.structured_output != "json_schema":
        content["response_contract"] = {"status": "ok"} if response_kind == "probe" else contract()
    body = {
        "model": config.model,
        "messages": [{"role": "user", "content": json.dumps(content, ensure_ascii=False, separators=(",", ":"))}],
        "response_format": ({"type": "json_schema", "json_schema": {"name": "connection_probe" if response_kind == "probe" else "operations_analysis", "strict": True, "schema": schema}}
            if config.structured_output == "json_schema" else {"type": "json_object"}),
        "max_completion_tokens": config.max_output_tokens,
        "store": False,
    }
    if not 256 <= config.max_output_tokens <= 16000 or config.structured_output not in ("json_schema", "json_object"):
        raise ProviderError("Chọn JSON Schema/JSON object và giới hạn đầu ra 256–16.000 token.")
    if config.provider not in ("OpenAI", "Groq"):
        body.pop("store")
        body["max_tokens"] = body.pop("max_completion_tokens")
    if config.provider == "Groq":
        body.pop("store")
    if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > 512_000:
        raise ProviderError("Payload vượt 512 KB; hãy thu hẹp phạm vi hoặc rút gọn chỉ dẫn phiên.")
    started = time.monotonic()
    deadline = deadline if deadline is not None else started + timeout
    estimated = estimate_tokens(body)
    try:
        # Enforce a wall deadline even if a server streams data slowly. Each
        # blocking read is additionally bounded; never log body/exception/headers.
        for attempt in range(2 if budget else 1):
            if budget:
                budget.acquire(estimated, deadline)
            remaining = min(timeout, deadline-time.monotonic())
            attempt_started = time.monotonic()
            if remaining <= 0:
                raise ProviderError("Hết thời gian gọi model.")
            if client is None:
                result = asyncio.run(_fetch_async(ENDPOINTS[config.provider], headers, body, remaining))
                status_code, response_bytes = result[:2]
                retry_after = result[2] if len(result) > 2 else None
            else:
                with client.stream("POST", ENDPOINTS[config.provider], headers=headers, json=body, timeout=remaining) as response:
                    status_code, retry_after = response.status_code, response.headers.get("retry-after")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if time.monotonic() >= min(attempt_started+timeout, deadline):
                            raise ProviderError("Quá thời gian gọi model.")
                        if size > 256_000:
                            raise ProviderError("Phản hồi API vượt giới hạn 256 KB.")
                        chunks.append(chunk)
                    response_bytes = b"".join(chunks)
            if status_code == 429 and budget and attempt == 0:
                try:
                    delay = float(retry_after)
                except (ValueError, TypeError):
                    delay = -1
                if 0 <= delay <= 10 and time.monotonic()+delay+1 < deadline:
                    budget.sleep(delay)
                    continue
            break
        if status_code != 200:
            if budget and status_code in (401, 403, 429):
                budget.halted = True
                if hasattr(budget, "provider_failed"):
                    budget.provider_failed(status_code, retry_seconds(retry_after))
            fixes = {400: "Kiểm tra model, JSON Schema/JSON mode và giới hạn token; provider không hỗ trợ có thể cần JSON object.",
                401: "Kiểm tra khóa của đúng provider.", 403: "Tài khoản/model chưa có quyền truy cập.",
                404: "Kiểm tra định danh model hoặc model Ollama đã cài.", 429: "Hết quota/rate limit; xem Limits của tài khoản, chờ kỳ hạn mức mới hoặc thu hẹp lượt. Không tự nâng gói.",
                500: "Provider đang lỗi; thử lại một phiên mới khi sẵn sàng.", 503: "Provider/model chưa sẵn sàng."}
            raise ProviderError(f"API trả HTTP {status_code}. " + fixes.get(status_code, "Kiểm tra trạng thái provider.") + " Không dùng phản hồi giả.",
                status_code=status_code, retry_after=retry_seconds(retry_after) if status_code == 429 else None)
        response_data = json.loads(response_bytes)
        choice = response_data["choices"][0]
        if choice.get("finish_reason") in ("length", "content_filter") or choice.get("message", {}).get("refusal"):
            raise ProviderError("Model từ chối hoặc hết token trước khi hoàn tất; không dùng đầu ra dở dang.")
        text = response_data["choices"][0]["message"]["content"]
        if not isinstance(text, str):
            raise ProviderError("API không trả nội dung văn bản JSON.")
        data = json.loads(text)
        normalized = json.dumps(data, ensure_ascii=False)
        if (config.api_key and config.api_key in normalized) or (len(config.instructions) >= 24 and config.instructions in normalized):
            raise ProviderError("Đầu ra chứa khóa hoặc nguyên chỉ dẫn phiên; đã chặn hiển thị.")
        if response_kind == "probe":
            if not isinstance(data, dict) or data != {"status": "ok"}:
                raise ProviderError("Phản hồi kiểm tra kết nối phải là JSON {\"status\":\"ok\"}.")
            output = dict(data)
        else:
            output = validate_output(data, set(payload["metrics"]))
        actual_model = response_data.get("model")
        if not isinstance(actual_model, str) or not actual_model:
            raise ProviderError("API không trả định danh model thực tế.")
        if config.api_key and config.api_key in actual_model:
            raise ProviderError("Định danh model không an toàn; đã chặn hiển thị.")
        usage = response_data.get("usage", {})
        output["_usage"] = {k: v for k, v in usage.items() if k in ("prompt_tokens", "completion_tokens", "total_tokens")
                            and isinstance(v, int) and not isinstance(v, bool) and v >= 0} if isinstance(usage, dict) else {}
        if budget and hasattr(budget, "provider_succeeded"):
            budget.provider_succeeded()
        return output, actual_model
    except (httpx.TimeoutException, TimeoutError):
        raise ProviderError("API timeout; không dùng phản hồi giả.") from None
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        raise ProviderError("Lỗi kết nối hoặc JSON API không hợp lệ; không dùng phản hồi giả.") from None


def probe_model(config, client=None, window=None):
    """Synthetic probe; accept config only, never session datasets/instructions."""
    from dataclasses import replace
    safe = replace(config, instructions="", max_output_tokens=768)
    budget = RequestBudget(max_calls=2, max_tokens=8000, window=window) if window is not None else RequestBudget(max_calls=2, max_tokens=8000)
    payload = {"data_label": "DEMO SYNTHETIC PROBE", "sample": 1,
        "task": 'Connection check only. Return exactly the JSON object {"status":"ok"}.'}
    try:
        answer, model = call_model(safe, payload, timeout=15, client=client, budget=budget, response_kind="probe")
        return {"ok": True, "model": model, "usage": answer.get("_usage", {}), "attempts": budget.attempts}
    except ProviderError as exc:
        # Only transport-generated safe errors; never return request/response.
        return {"ok": False, "error": str(exc), "attempts": budget.attempts}
