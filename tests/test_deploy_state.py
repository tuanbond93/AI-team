import time

from ops_team.deploy_state import health, restore, save
from ops_team.telegram_state import BusinessState


class Responder:
    def __init__(self, code):
        self.code = code

    def _plan_code(self):
        return self.code


def test_restore_keeps_owner_approval_only_when_plan_is_unchanged(tmp_path):
    state = BusinessState(tmp_path/'v')
    try:
        state.set_control('worker_enabled', True)
        state.set_control('schedule_enabled', True)
        state.set_control('approved_plan', 'P-1')
        saved = save(state)
        assert saved['schedule_enabled'] and saved['had_approved_plan']
        state.set_control('worker_enabled', False)
        state.set_control('schedule_enabled', False)  # what stop_worker does
        assert 'Đã bật lại' in restore(state, Responder('P-1'), saved, worker=False)
        assert state.control('schedule_enabled') is True and state.control('worker_enabled') is False
        assert '/enable' in restore(state, Responder('P-2'), saved)
        assert state.control('schedule_enabled') is False and state.control('worker_enabled') is True
        assert state.control('approved_plan') == 'P-1'  # never writes a new approval
        paused = {**saved, 'schedule_enabled': False}
        assert 'tạm dừng' in restore(state, Responder('P-1'), paused)
        assert state.control('schedule_enabled') is False
    finally:
        state.close()


def test_health_needs_pid_fresh_heartbeat_and_no_error(tmp_path):
    state = BusinessState(tmp_path/'v')
    try:
        assert not health(state)[0]
        state.set_control('worker_pid', 42)
        state.set_control('heartbeat', time.time())
        state.set_control('last_error', '')
        assert health(state)[0]
        state.set_control('heartbeat', time.time()-600)
        assert not health(state)[0]
        state.set_control('heartbeat', time.time())
        state.set_control('last_error', 'Chưa kết nối được Telegram')
        ok, line = health(state)
        assert not ok and 'Telegram' in line
    finally:
        state.close()
