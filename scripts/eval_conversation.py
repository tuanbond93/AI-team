"""Conversation evaluation: run the Vietnamese scenarios against a code root, score with a rubric.

  python scripts/eval_conversation.py --root . --label new --out docs/eval
  python scripts/eval_conversation.py --root <worktree of an older commit> --label baseline --out docs/eval
  python scripts/eval_conversation.py --compare docs/eval/baseline.json docs/eval/new.json --out docs/eval

Code commands (/chiphi, /thieu, ...) are replaced by fixed synthetic outputs so the
test measures the conversation layer, not the data. No network, no model, no real
data; the vault lives in a temp folder with a test-only cipher.
Automatic scores are a screen, not proof: ``blind_review.csv`` is for people.
"""
import argparse
import csv
import json
import os
import random
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FIXED = datetime(2026, 10, 29, 10, 0).timestamp()  # Thursday; scenarios use this "today"
OWNER, MEMBER = 123, 456
BANNED = ['chắc chắn rồi', 'câu hỏi rất hay', 'tôi rất vui', 'rất vui được hỗ trợ', 'cảm ơn anh đã liên hệ',
          'chưa có ai/model', 'kết luận:', '/details', '/thieu', 'xin lỗi vì sự bất tiện']


def canned(command, args):
    span = ' '.join(args.split()) or 'kỳ hiện tại'
    return {
        'chiphi': f'Kết luận: mới tính được phần đủ nguồn; còn 2 nhóm dữ liệu thiếu nên chưa có tổng chi phí. /details xem tiếp.\n\n'
                  f'DỰ KIẾN TRƯỚC VAT {span} — phần đủ nguồn.\nDoanh thu dự kiến phần đủ nguồn: 1.250.000đ (90 sản phẩm)\nChi phí xe: 830.500đ\nBốc xếp: N/A',
        'thieu': 'CẦN ANH BỔ SUNG: 2 nhóm dữ liệu. Phần này để trống, không tính là 0.\n• CS bổ sung cột L — Booking dòng 14, 15\n• Raw thiếu biển S — Raw dòng 220',
        'sla': f'Kết luận SLA {span}: 95% đúng hạn (19/20 đơn).\nTrễ: 1 đơn Sơn La (D+3).\n/details xem tiếp.',
        'xe': 'V1 29C-123.45: 2 chuyến, 1.840 kg, tỷ trọng 76%\nV2 22A-222.22: 1 chuyến, 950 kg, giao thẳng',
        'run': 'Kết luận: số kỳ này còn 2 nhóm dữ liệu thiếu.\nCẦN CS BỔ SUNG: kg cột L cho 2 đợt.\nBƯỚC TIẾP: CS bổ sung rồi xuất lại tab.',
        'details': 'Nguồn: Booking ghim tab CS; Raw ghim tab Data Supra.\nCông thức chi phí xe: chi phí xe/ngày chia theo kg.',
        'duyet': 'XEM TRƯỚC — P1A2B3C: GHN 123 ngày 10/10 cộng 2 phần kg cột L (5 + 7 kg).\nChỉ đợt này được cộng; các đợt khác giữ nguyên. Gửi /duyet P1A2B3C xacnhan để lưu.',
    }.get(command)


def load(root):
    sys.path.insert(0, str(Path(root).resolve()))
    for name in [m for m in sys.modules if m == 'ops_team' or m.startswith('ops_team.')]:
        del sys.modules[name]
    import ops_team.telegram_assistant as ta
    from ops_team import telegram_secrets, telegram_state
    prefix = b'TEST-ONLY-NOT-DPAPI\0'
    def fake(data, decrypt=False):
        if decrypt:
            return bytes(b ^ 0x5A for b in data[len(prefix):])
        return prefix+bytes(b ^ 0x5A for b in data)
    if os.name != 'nt':
        telegram_secrets._dpapi = fake

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(FIXED, tz)
    ta.datetime = Frozen
    return ta, telegram_state


def make(ta, telegram_state, folder, calls):
    state = telegram_state.BusinessState(folder)
    r = ta.AssistantResponder(state, {'owner_id': OWNER, 'models': {}})
    has_inner = hasattr(r, '_answer')
    original = r._answer if has_inner else r.answer

    def stub(request):
        out = canned(request.command, request.text)
        calls.append((request.command, request.text.strip()))
        return out if out is not None else original(request)
    if has_inner:
        r._answer = stub
        try:
            from ops_team.conversation import Conversation
            r._conv = Conversation(r, clock=lambda: FIXED)
        except ImportError:
            pass
        entry = r.answer
    else:
        # Oldest versions: answer() is the only entry; plain text always went to the model path.
        def entry(request):
            return stub(request) if request.command != 'ask' else original(request)
    return state, entry


def run(root, label, scenarios_file='tests/conversation_eval/scenarios.json'):
    ta, telegram_state = load(root)
    from ops_team.telegram_bridge import Incoming
    scenarios = json.loads((HERE/scenarios_file).read_text(encoding='utf-8'))['scenarios']
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for s in scenarios:
            calls = []
            state, entry = make(ta, telegram_state, Path(tmp)/s['id'], calls)
            turns = []
            try:
                for i, turn in enumerate(s['turns']):
                    user = turn.get('user_id', s.get('user_id', OWNER))
                    before = len(calls)
                    started = time.perf_counter()
                    try:
                        reply = entry(Incoming(ta.GROUP_ID, user, 1000+i, None, 'ask', turn['user']))
                    except Exception as exc:  # a crash is a failed turn, not an aborted run
                        reply = f'[LỖI {type(exc).__name__}]'
                    elapsed = (time.perf_counter()-started)*1000
                    made = calls[before:]
                    work = [c for c in made if c[0] != 'ask']
                    turns.append({'user': turn['user'], 'reply': reply, 'ms': round(elapsed, 2), 'command': work[-1][0] if work else None,
                                  'args': work[-1][1] if work else None, 'legacy_path': any(c[0] == 'ask' for c in made), 'expect': turn.get('expect', {})})
            finally:
                state.close()
            results.append({'id': s['id'], 'group': s['group'], 'turns': turns})
    return {'label': label, 'root': str(root), 'results': results, 'scores': score(results)}


def check_turn(t):
    """(dimension, passed, why) for each explicit expectation of one turn."""
    e, reply, out = t['expect'], t['reply'] or '', []
    low = reply.casefold()
    dims = e.get('dims', ['relevance'])
    if 'command' in e:
        ok = t['command'] == e['command'] and (e['command'] is not None or not e.get('route') or t['legacy_path'])
        out.append(('relevance' if 'relevance' in dims or 'continuity' not in dims else 'continuity', ok, f"lệnh={t['command']} cần={e['command']}"))
    if 'args' in e:
        out.append(('continuity', t['args'] == e['args'], f"args={t['args']} cần={e['args']}"))
    for needle in e.get('any', []) and [e['any']]:
        out.append((dims[-1] if dims[-1] != 'relevance' else 'accuracy', any(n.casefold() in low for n in needle), 'cần có: '+' | '.join(needle)))
    for needle in e.get('none', []):
        out.append(('accuracy' if 'accuracy' in dims else dims[0], needle.casefold() not in low, 'không được có: '+needle))
    if 'max_chars' in e:
        out.append(('naturalness', len(reply) <= e['max_chars'], f'dài {len(reply)} > {e["max_chars"]}'))
    if t['command'] and canned(t['command'], t['args'] or ''):
        # Body lines only: the first line and IN-HOA section headers carry the period, restated by the opener.
        body = [l for l in canned(t['command'], t['args'] or '').split('\n')[1:] if not re.match(r'^[A-ZÀ-Ỹ ]{6,}', l)]
        numbers = re.findall(r'\d[\d.,%]*', '\n'.join(body))
        out.append(('accuracy', all(n in reply for n in numbers), 'giữ nguyên mọi con số của code'))
    return out


def score(results):
    dims = {k: [0, 0] for k in ('naturalness', 'relevance', 'continuity', 'accuracy', 'persona')}
    replies = [t['reply'] or '' for r in results for t in r['turns']]
    for r in results:
        for t in r['turns']:
            t['checks'] = [{'dim': d, 'ok': ok, 'why': why} for d, ok, why in check_turn(t)]
            reply = (t['reply'] or '').casefold()
            # Global naturalness/persona screens on every reply.
            t['checks'].append({'dim': 'naturalness', 'ok': not any(b in reply for b in BANNED), 'why': 'không có câu/nhãn rập khuôn'})
            first = (t['reply'] or '').split('\n', 1)[0]
            t['checks'].append({'dim': 'naturalness', 'ok': not re.match(r'^[A-ZÀ-Ỹ\s—:]{12,}', first), 'why': 'dòng đầu không phải tiêu đề IN HOA'})
            t['checks'].append({'dim': 'persona', 'ok': not re.search(r'\btôi là (người|nhân viên)\b|\bem là người\b', reply), 'why': 'không nhận là người'})
            for c in t['checks']:
                dims[c['dim']][0] += c['ok']
                dims[c['dim']][1] += 1
    openers = [' '.join(re.findall(r'\w+', r)[:3]).casefold() for r in replies if r]
    diversity = len(set(openers))/len(openers) if openers else 0
    question_end = sum(r.rstrip().endswith('?') for r in replies)/len(replies) if replies else 0
    out = {k: round(v[0]/v[1], 3) if v[1] else None for k, v in dims.items()}
    out.update({'opener_diversity': round(diversity, 3), 'ends_with_question': round(question_end, 3),
                'checks': sum(v[1] for v in dims.values()), 'passed': sum(v[0] for v in dims.values()),
                'median_ms': round(sorted(t['ms'] for r in results for t in r['turns'])[len(replies)//2], 2) if replies else None})
    return out


def compare(paths, out):
    runs = [json.loads(Path(p).read_text(encoding='utf-8')) for p in paths]
    keys = ['naturalness', 'relevance', 'continuity', 'accuracy', 'persona', 'opener_diversity', 'ends_with_question', 'passed', 'checks', 'median_ms']
    lines = ['| Tiêu chí | '+' | '.join(r['label'] for r in runs)+' |', '|---|'+'---|'*len(runs)]
    for k in keys:
        lines.append(f'| {k} | '+' | '.join(str(r['scores'].get(k)) for r in runs)+' |')
    rows, rng = [], random.Random(20261029)
    base, new = runs[0], runs[-1]
    key = []
    for a, b in zip(base['results'], new['results']):
        for i, (ta, tb) in enumerate(zip(a['turns'], b['turns'])):
            pair = [('A', ta['reply']), ('B', tb['reply'])]
            flipped = rng.random() < 0.5
            x, y = (pair[1], pair[0]) if flipped else pair
            rows.append({'kịch bản': a['id'], 'lượt': i+1, 'người dùng nói': ta['user'], 'trả lời X': x[1], 'trả lời Y': y[1],
                         'X tự nhiên (1-5)': '', 'Y tự nhiên (1-5)': '', 'X đúng ý (1-5)': '', 'Y đúng ý (1-5)': '', 'chọn bản tốt hơn (X/Y/ngang)': ''})
            key.append({'kịch bản': a['id'], 'lượt': i+1, 'X': base['label'] if not flipped else new['label'], 'Y': new['label'] if not flipped else base['label']})
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out/'blind_review.csv', 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (out/'blind_key.json').write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding='utf-8')
    failures = []
    for r in new['results']:
        for i, t in enumerate(r['turns']):
            failures += [f"{r['id']} lượt {i+1}: {c['why']}" for c in t['checks'] if not c['ok']]
    table = '\n'.join(lines)
    (out/'comparison.md').write_text(table+'\n\nCheck chưa đạt ở bản cuối:\n'+('\n'.join('- '+f for f in failures) or '- không có')+'\n', encoding='utf-8')
    return table, failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=str(HERE))
    parser.add_argument('--label', default='new')
    parser.add_argument('--out', default=str(HERE/'docs/eval'))
    parser.add_argument('--compare', nargs='+')
    parser.add_argument('--scenarios', default='tests/conversation_eval/scenarios.json')
    args = parser.parse_args()
    if args.compare:
        table, failures = compare(args.compare, args.out)
        print(table)
        print('\n'.join(failures) or 'Không có check chưa đạt.')
        return
    result = run(args.root, args.label, args.scenarios)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out)/f'{args.label}.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(result['scores'], ensure_ascii=False))


if __name__ == '__main__':
    main()
