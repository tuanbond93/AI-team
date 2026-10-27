"""Tests must never read or remove the user's Telegram vault or worker state."""
import os

import pytest

_FAKE_PREFIX = b"TEST-ONLY-NOT-DPAPI\0"


def _fake_dpapi(data, decrypt=False):
    # Reversible test cipher for non-Windows dev runs; never used by the app itself.
    if decrypt:
        if not data.startswith(_FAKE_PREFIX):
            raise RuntimeError("Không mở được DPAPI bằng tài khoản Windows hiện tại.")
        return bytes(b ^ 0x5A for b in data[len(_FAKE_PREFIX):])
    return _FAKE_PREFIX+bytes(b ^ 0x5A for b in data)


@pytest.fixture(autouse=True)
def isolated_telegram_state(tmp_path, monkeypatch):
    from ops_team import telegram_state, telegram_secrets, telegram_control_ui
    root = tmp_path/"isolated-telegram"
    monkeypatch.setattr(telegram_state, "ROOT", root)
    monkeypatch.setattr(telegram_secrets, "ROOT", root)
    monkeypatch.setattr(telegram_control_ui, "ROOT", root)
    if os.name != "nt":
        monkeypatch.setattr(telegram_secrets, "_dpapi", _fake_dpapi)
    return root
