"""CLI for scripts/deploy/Deploy-Bot.ps1: save and restore worker/schedule controls around a code update.

python -m ops_team.deploy_state save --root <vault> --out <file.json>
python -m ops_team.deploy_state restore --root <vault> --saved <file.json>
python -m ops_team.deploy_state health --root <vault>

Restore never creates a new approval: the schedule is turned back on only if it
was on before AND the owner's approved plan code still equals the current plan
code (the same check tick() makes before every send). Otherwise it stays paused
and the owner sends /plan then /enable.
"""
import argparse
import json
import sys
import time
from pathlib import Path


def save(state):
    return {'worker_enabled': bool(state.control('worker_enabled', False)),
            'schedule_enabled': bool(state.control('schedule_enabled', False)),
            'had_approved_plan': bool(state.control('approved_plan')), 'saved_at': time.time()}


def restore(state, responder, saved, *, worker=True):
    """Set controls back. Returns a short Vietnamese status line."""
    state.set_control('worker_enabled', bool(worker))
    if not saved.get('schedule_enabled'):
        state.set_control('schedule_enabled', False)
        return 'Lịch 13h trước khi cập nhật đang tạm dừng; giữ nguyên tạm dừng.'
    code = state.control('approved_plan')
    if code and code == responder._plan_code():
        state.set_control('schedule_enabled', True)
        return 'Đã bật lại lịch 13h theo kế hoạch owner đã duyệt (mã kế hoạch không đổi).'
    state.set_control('schedule_enabled', False)
    return 'Kế hoạch đã đổi hoặc chưa duyệt nên lịch 13h đang TẠM DỪNG: owner gửi /plan rồi /enable <mã> trong nhóm.'


def health(state, max_age=180):
    """(ok, line) for the running worker: pid, heartbeat age, last error."""
    pid, beat, error = state.control('worker_pid'), state.control('heartbeat'), state.control('last_error', '')
    age = time.time()-beat if isinstance(beat, (int, float)) else None
    ok = bool(pid) and age is not None and age <= max_age and not error
    return ok, f"worker_pid={pid or 'không có'}; heartbeat={'chưa có' if age is None else str(int(age))+' giây trước'}; lỗi={error or 'không'}"


def main(argv=None):
    from .telegram_state import BusinessState
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('save', 'restore', 'health'))
    parser.add_argument('--root', required=True)
    parser.add_argument('--out')
    parser.add_argument('--saved')
    parser.add_argument('--no-worker', action='store_true', help='restore controls but keep the worker disabled')
    args = parser.parse_args(argv)
    state = BusinessState(Path(args.root))
    try:
        if args.action == 'save':
            Path(args.out).write_text(json.dumps(save(state)), encoding='utf-8')
            print('OK: đã lưu trạng thái worker/lịch.')
            return 0
        if args.action == 'restore':
            from .telegram_assistant import AssistantResponder
            from .telegram_secrets import load_settings
            settings = {k: v for k, v in load_settings(Path(args.root)).items() if k != 'bot_token'}
            saved = json.loads(Path(args.saved).read_text(encoding='utf-8'))
            print('OK: '+restore(state, AssistantResponder(state, settings), saved, worker=not args.no_worker))
            return 0
        ok, line = health(state)
        print(('OK: ' if ok else 'LỖI: ')+line)
        return 0 if ok else 1
    except Exception as exc:
        print('LỖI: '+type(exc).__name__+': '+str(exc)[:200])
        return 1
    finally:
        state.close()


if __name__ == '__main__':
    sys.exit(main())
