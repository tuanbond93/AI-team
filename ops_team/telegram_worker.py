"""Independent, single-instance Windows worker. Reads only this app's DPAPI vault."""
from contextlib import contextmanager
from pathlib import Path
import os
import sys
import time
from .telegram_state import BusinessState, ROOT, GROUP_ID
from .telegram_secrets import load_settings
from .telegram_bridge import TelegramAPI, TelegramBridge, TelegramError
from .telegram_assistant import AssistantResponder


@contextmanager
def single_instance(root=None):
    import msvcrt
    root = Path(root) if root is not None else ROOT
    root.mkdir(parents=True, exist_ok=True)
    stream = open(root/"worker.lock", "a+b")
    stream.seek(0)
    if stream.read(1) == b"":
        stream.write(b"1")
        stream.flush()
    stream.seek(0)
    try:
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        stream.close()
        raise RuntimeError("Worker đã chạy ở process khác.") from None
    try:
        yield
    finally:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        stream.close()


def run(root=None):
    root = Path(root) if root is not None else ROOT
    state, bridge = None, None
    with single_instance(root):
        try:
            settings = load_settings(root)
            if settings.get("chat_id") != GROUP_ID:
                raise ValueError("Nhóm chưa được cho phép.")
            state = BusinessState(root)
            if not state.control("worker_enabled", False):
                return
            state.set_control("worker_pid", os.getpid())
            state.set_control("last_error", "")
            responder = AssistantResponder(state, settings)
            bridge = TelegramBridge(TelegramAPI(settings["bot_token"]), responder, settings.get("users", []),
                lifetime=None, expected_chat=GROUP_ID, owner_id=settings.get("owner_id"), state=state)
            failures = 0
            while state.control("worker_enabled", False):
                try:
                    bridge.start()
                    break
                except TelegramError as exc:
                    if not exc.transient:
                        raise
                    failures += 1
                    state.set_control("heartbeat", time.time())
                    state.set_control("last_error", "Chưa kết nối được Telegram; worker đang chờ mạng và sẽ tự thử lại.")
                    if bridge._backoff(min(60, 2**min(failures, 6))):
                        return
            while bridge.snapshot()["alive"]:
                time.sleep(1)
        except Exception as exc:
            if state:
                state.set_control("last_error", str(exc) if isinstance(exc, TelegramError) else "Chưa khởi động được worker. Kiểm tra token/quyền mạng hoặc cấu hình DPAPI trong AI Team.")
            raise
        finally:
            if bridge:
                bridge.stop()
                if bridge._thread:
                    bridge._thread.join(timeout=60)
            if state:
                state.set_control("worker_pid", None)
                state.close()


def main():
    try:
        run()
    except Exception:
        # Never print exception objects; request URLs may carry the bot token.
        print("Telegram worker chưa chạy được hoặc đã có worker khác. Xem trạng thái trong AI Team.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
