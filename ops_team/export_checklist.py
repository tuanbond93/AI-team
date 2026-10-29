"""CLI: write the /thieu checklist as Excel on the owner's machine.

python -m ops_team.export_checklist --out "C:\\path\\thieu.xlsx" [--root <vault copy>] [YYYY-MM-DD YYYY-MM-DD]
Read-only on the vault (a roll to a new month may still run as in /thieu);
no Telegram, no model, nothing written except --out.
"""
import argparse
import sys
from pathlib import Path


def main():
    from .telegram_state import BusinessState
    from .telegram_secrets import load_settings
    from .telegram_assistant import AssistantResponder
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--root', help='Vault folder (use a temporary copy for trials)')
    parser.add_argument('dates', nargs='*')
    args = parser.parse_args()
    state = None
    try:
        root = Path(args.root) if args.root else None
        state = BusinessState(root) if root else BusinessState()
        settings = load_settings(root)
        responder = AssistantResponder(state, {k: v for k, v in settings.items() if k != 'bot_token'})
        data = responder.checklist_workbook(' '.join(args.dates))
        out = Path(args.out)
        if out.suffix.lower() != '.xlsx':
            raise ValueError('--out phải là file .xlsx')
        out.write_bytes(data)
        print(f'Đã ghi {out} ({len(data)} byte).')
        return 0
    except Exception as exc:
        print('Chưa xuất được: '+str(exc)[:300])
        return 1
    finally:
        if state:
            state.close()


if __name__ == '__main__':
    sys.exit(main())
