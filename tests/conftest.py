"""Tests must never read or remove the user's Telegram vault or worker state."""
import pytest


@pytest.fixture(autouse=True)
def isolated_telegram_state(tmp_path, monkeypatch):
    from ops_team import telegram_state, telegram_secrets, telegram_control_ui
    root = tmp_path/"isolated-telegram"
    monkeypatch.setattr(telegram_state, "ROOT", root)
    monkeypatch.setattr(telegram_secrets, "ROOT", root)
    monkeypatch.setattr(telegram_control_ui, "ROOT", root)
    return root
