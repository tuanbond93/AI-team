"""Windows CurrentUser DPAPI. Never writes plaintext secret/config/source bytes."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
from .telegram_state import ROOT


def _dpapi(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("Kho bí mật này cần Windows DPAPI CurrentUser.")
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
    raw = ctypes.create_string_buffer(data)
    inp = Blob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)))
    out = Blob()
    function = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(inp), None, None, None, None, 1, ctypes.byref(out)):
        raise RuntimeError("Không mở được DPAPI bằng tài khoản Windows hiện tại.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def save_protected(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _dpapi(data)
    fd, temporary = tempfile.mkstemp(prefix=".encrypted-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_protected(path):
    return _dpapi(Path(path).read_bytes(), decrypt=True)


def save_settings(settings, root=None):
    # All custom instructions/skills deliberately remain in UI RAM.
    allowed = {"bot_token", "owner_id", "chat_id", "users", "models", "allowed_folder", "google_service_account"}
    if set(settings)-allowed:
        raise ValueError("Cấu hình chứa trường chưa được phép lưu.")
    if any("instructions" in model for model in settings.get("models", {}).values()):
        raise ValueError("Không lưu chỉ dẫn/skill phiên vào kho cấu hình.")
    if settings.get("chat_id") != -5395485115:
        raise ValueError("Chỉ nhóm thử nghiệm đã chỉ định được phép dùng cấu hình này.")
    save_protected(Path(root or ROOT)/"settings.dpapi", json.dumps(settings).encode())


def load_settings(root=None):
    return json.loads(load_protected(Path(root or ROOT)/"settings.dpapi"))
