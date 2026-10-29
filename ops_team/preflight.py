"""CLI: code-only health check of the bot on a vault (use a copy before deploying).

python -m ops_team.preflight --root "<vault copy>" [YYYY-MM-DD YYYY-MM-DD]
Runs the owner commands in memory (no Telegram send, no model call) and prints
one line per check: OK/LỖI and the reply's first line. Exit code 1 if any check
fails, so it can gate a deployment.
"""
import argparse
import sys
from pathlib import Path


def run_checks(responder, owner, dates=''):
    from .telegram_bridge import Incoming
    from .telegram_state import GROUP_ID
    def no_model(**kwargs):
        raise RuntimeError('preflight không gọi AI')
    responder._get_agent = no_model
    results = []
    commands = [('chiphi', dates), ('thieu', dates), ('xe', dates), ('sla', dates), ('duyet', ''), ('status', '')]
    for i, (command, text) in enumerate(commands, 1):
        try:
            reply = responder.answer(Incoming(GROUP_ID, owner, 900000+i, None, command, text))
            bad = reply.startswith(('Chưa hoàn tất xử lý', 'Chưa tính được', 'Chi phí: chưa'))
            results.append((not bad, '/'+command, reply.split('\n')[0][:160]))
        except Exception as exc:
            results.append((False, '/'+command, type(exc).__name__+': '+str(exc)[:160]))
    try:
        report = responder.report()
        results.append((not report.startswith('CHƯA CÓ BÁO CÁO'), 'báo cáo 13h', report.split('\n')[0][:160]))
    except Exception as exc:
        results.append((False, 'báo cáo 13h', type(exc).__name__+': '+str(exc)[:160]))
    try:
        _, code, missing = responder.plan()
        results.append((True, 'kế hoạch', ('còn thiếu: '+'; '.join(missing)) if missing else 'đủ điều kiện'))
    except Exception as exc:
        results.append((False, 'kế hoạch', type(exc).__name__+': '+str(exc)[:160]))
    return results


def main():
    from .telegram_state import BusinessState
    from .telegram_secrets import load_settings
    from .telegram_assistant import AssistantResponder
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, help='Bản sao vault (không dùng vault đang chạy)')
    parser.add_argument('dates', nargs='*')
    args = parser.parse_args()
    state = None
    try:
        root = Path(args.root)
        if (root/'worker.lock').exists():
            print('LỖI: thư mục có worker.lock — hãy dùng bản sao vault, không dùng vault của bot đang chạy.')
            return 1
        state = BusinessState(root)
        settings = {k: v for k, v in load_settings(root).items() if k != 'bot_token'}
        responder = AssistantResponder(state, settings)
        results = run_checks(responder, settings['owner_id'], ' '.join(args.dates))
        for ok, name, line in results:
            print(('OK   ' if ok else 'LỖI ')+name+': '+line)
        return 0 if all(ok for ok, _, _ in results) else 1
    except Exception as exc:
        print('LỖI: '+str(exc)[:300])
        return 1
    finally:
        if state:
            state.close()


if __name__ == '__main__':
    sys.exit(main())
