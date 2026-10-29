"""Understand plain Vietnamese messages and map them to the existing code commands.

Deterministic keyword + date parsing only: no model call, no state change.
Commands that change data (confirming exceptions, keeping prices) are never
executed from free text; they are previewed and the owner confirms explicitly.
Questions asking why/what to do are left to the AI path (with code metrics).
"""
import re
import unicodedata
from calendar import monthrange
from datetime import date, timedelta


def _plain(text):
    text = unicodedata.normalize('NFD', str(text).casefold())
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn').replace('đ', 'd')
    return ' '.join(text.split())


# (intent, patterns on de-accented lowercase text); first match wins.
INTENTS = [
    ('thieu', [r'\bthieu\b', r'bo sung', r'loi du lieu', r'kiem tra (du lieu|data)', r'check data', r'can nhap']),
    ('sla', [r'\bsla\b', r'dung han', r'tre han', r'giao tre', r'giao muon', r'dung gio']),
    ('duyet', [r'\bduyet\b', r'cho duyet', r'cong (phan )?kg']),
    ('giugia', [r'giu (nguyen )?gia']),
    ('chiphi', [r'chi phi', r'\bton (bao nhieu|het)', r'lai lo', r'\b(lai|lo) (bao nhieu|the nao|khong)\b', r'doanh thu', r'\bp ?& ?l\b', r'loi nhuan', r'\bcuoc\b', r'boc xep', r'bao nhieu tien']),
    ('xe', [r'\bxe\b', r'\bchuyen xe\b', r'bao nhieu chuyen', r'bien so', r'ty trong']),
    ('run', [r'bao cao', r'tong hop', r'\breport\b', r'tinh hinh']),
    ('details', [r'chi tiet', r'xem them', r'giai thich them', r'cu the hon']),
    ('thanks', [r'^(ok|oke|okay|vang|da|uh|u)?\s*(cam on|thank|thanks|tks)\b', r'^(ok|oke|okay|duoc roi|tot)( em| nhe| nha)?$']),
    ('help', [r'lam duoc gi', r'giup (duoc )?gi', r'huong dan', r'^(hi|hello|chao)\b']),
]
ANALYSIS = [r'tai sao', r'vi sao', r'co nen', r'\bnen\b.*(lam|giam|tang)', r'de xuat', r'lam sao (de|cho)', r'phan tich', r'nguyen nhan']


def _date(day_text, month_text, year_text, today):
    year = int(year_text) if year_text else today.year
    if year < 100:
        year += 2000
    return date(year, int(month_text), int(day_text))


def parse_dates(text, today):
    """Return 'YYYY-MM-DD YYYY-MM-DD' for the period mentioned, or '' (bot default scope)."""
    t = _plain(text)
    if 'hom kia' in t:
        d = today-timedelta(days=2)
        return f'{d} {d}'
    if 'hom qua' in t:
        d = today-timedelta(days=1)
        return f'{d} {d}'
    if 'hom nay' in t:
        return f'{today} {today}'
    if 'tuan truoc' in t:
        start = today-timedelta(days=today.weekday()+7)
        return f'{start} {start+timedelta(days=6)}'
    if 'tuan nay' in t:
        return f'{today-timedelta(days=today.weekday())} {today}'
    num = r'(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?'
    # "1-22/10", "1 den 22/10"
    m = re.search(r'\b(\d{1,2})\s*(?:-|den|toi|->)\s*(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?\b', t)
    if m and not re.search(num+r'\s*(?:-|den|toi|->)', t[:m.end()]):
        try:
            a, b = _date(m[1], m[3], m[4], today), _date(m[2], m[3], m[4], today)
            if a <= b:
                return f'{a} {b}'
        except ValueError:
            pass
    found = []
    for m in re.finditer(r'\b'+num+r'\b', t):
        try:
            found.append(_date(m[1], m[2], m[3], today))
        except ValueError:
            continue
    if len(found) >= 2:
        a, b = sorted(found[:2])
        return f'{a} {b}'
    if len(found) == 1:
        return f'{found[0]} {found[0]}'
    m = re.search(r'thang (\d{1,2})(?:[/\s](\d{4}))?', t)
    if m or 'thang nay' in t:
        month = int(m[1]) if m else today.month
        year = int(m[2]) if m and m[2] else today.year
        if not 1 <= month <= 12:
            return ''
        start = date(year, month, 1)
        end = min(date(year, month, monthrange(year, month)[1]), today)
        return f'{start} {end}' if start <= end else ''
    return ''


def route(text, today):
    """(command, args) for a plain message, or None when it needs the AI or is unclear."""
    t = _plain(text)
    if not t:
        return None
    analysis = any(re.search(p, t) for p in ANALYSIS)
    for intent, patterns in INTENTS:
        if any(re.search(p, t) for p in patterns):
            if analysis and intent not in ('thieu', 'details', 'help', 'thanks'):
                return None
            args = parse_dates(text, today) if intent in ('chiphi', 'xe', 'sla', 'thieu') else ''
            if intent == 'duyet':
                code = re.search(r'\bp[0-9a-f]{6}\b', t)
                args = code[0].upper() if code else ''
            return intent, args
    return None


def span_text(args):
    if not args:
        return 'kỳ này'
    a, b = args.split()
    fmt = lambda s: f'{s[8:10]}/{s[5:7]}'
    return 'ngày '+fmt(a) if a == b else 'từ '+fmt(a)+' đến '+fmt(b)


HINTS = [
    (r'gửi /thieu để xem (từng )?dòng cần bổ sung(, sửa nguồn rồi xuất lại đúng tab)?', 'anh nhắn "còn thiếu gì" là em liệt kê từng dòng'),
    (r'Owner gửi /thieu để xem đúng dòng', 'Anh nhắn "còn thiếu gì" là em liệt kê đúng dòng'),
    (r'/details xem (tiếp|chi tiết)\.?', 'Anh cần chi tiết thì nhắn "chi tiết" nhé.'),
    (r'/thieu xem từng dòng cần bổ sung; /details xem bảng số và quy tắc; /xe xem xe\.', 'Anh nhắn "còn thiếu gì", "chi tiết" hoặc "xe" nếu cần xem thêm.'),
    (r'Xem trước: /duyet <mã>\. Lưu: /duyet <mã> xacnhan\.', 'Anh nhắn "duyệt <mã>" để xem trước; khi chắc chắn thì gửi /duyet <mã> xacnhan để lưu.'),
]


def naturalize(command, reply, args=''):
    """Conversational framing around the code result; numbers and rows unchanged."""
    text = reply.strip()
    if text.startswith('Chưa hoàn tất: '):
        return 'Dạ, em chưa làm được: '+text[len('Chưa hoàn tất: '):]
    for pattern, repl in HINTS:
        text = re.sub(pattern, repl, text)
    span = span_text(args)
    if command == 'thieu':
        m = re.match(r'CẦN ANH BỔ SUNG(?: — [^:]+)?: (\d+) nhóm dữ liệu\. [^\n]*\n', text)
        if m:
            text = f'Dạ, {span} còn {m[1]} nhóm dữ liệu cần bổ sung ạ. Những phần này em để trống chứ không tính là 0:\n'+text[m.end():]
        elif text.startswith(('Dữ liệu', 'Chưa thấy dòng thiếu')):
            text = f'Dạ, {span} không còn dòng nào bị thiếu ạ. '+text
        else:
            text = 'Dạ, '+text[0].lower()+text[1:]
    elif command == 'chiphi':
        text = re.sub(r'^Kết luận: ', f'Dạ, em tính chi phí {span} rồi ạ: ', text)
        text = re.sub(r'\n\nDỰ KIẾN TRƯỚC VAT [^\n]*', '\n\nSố dưới đây là dự kiến trước VAT, chỉ gồm phần đã đủ nguồn.', text)
        text = re.sub(r'\n\nCHI PHÍ DỰ KIẾN [^\n]*', '\n\nSố dưới đây là chi phí dự kiến, chỉ gồm phần đã đủ nguồn, chưa phải thực chi.', text)
        text = text.replace('TỔNG DỰ KIẾN trước VAT', 'Tổng dự kiến trước VAT')
    elif command == 'sla':
        text = re.sub(r'^Kết luận SLA [^:]+: ', f'Dạ, SLA {span}: ', text)
    elif command == 'run':
        text = re.sub(r'^Kết luận: ', 'Dạ, tình hình kỳ này: ', text)
        text = text.replace('CẦN CS BỔ SUNG:', 'Cần CS bổ sung:').replace('CẦN ĐỐI CHIẾU:', 'Cần đối chiếu:').replace('BƯỚC TIẾP:', 'Bước tiếp theo:')
    elif command == 'xe':
        text = 'Dạ, tình hình xe '+span+':\n'+text
    elif command == 'details':
        text = 'Dạ, chi tiết đây ạ:\n'+text
    elif command == 'duyet':
        text = text.replace('ĐỀ XUẤT CHỜ ANH DUYỆT', 'Dạ, đây là các đợt đang chờ anh duyệt').replace('XEM TRƯỚC — ', 'Dạ, em xem trước giúp anh: ')
    return text


HELP = ('Dạ, anh cứ nhắn tự nhiên là được, ví dụ:\n'
        '• "chi phí tuần này" hoặc "tháng 10 lãi lỗ thế nào"\n'
        '• "còn thiếu dữ liệu gì không"\n'
        '• "SLA hôm qua" hoặc "giao trễ từ 1/10 đến 15/10"\n'
        '• "xe ngày 15/10"\n'
        '• "báo cáo hôm nay", "chi tiết"\n'
        'Em tính bằng số liệu nguồn; chỗ nào thiếu em sẽ nói rõ thiếu gì, không tự điền 0.')

UNCLEAR = ('Dạ, em chưa chắc anh cần xem phần nào. Em đang tính được bằng số liệu nguồn các việc sau, anh nhắn tự nhiên là được:\n'
           '• chi phí, lãi lỗ (vd "chi phí tuần này")\n• dữ liệu còn thiếu (vd "còn thiếu gì không")\n'
           '• SLA giao hàng (vd "SLA hôm qua")\n• tình hình xe (vd "xe ngày 15/10")\n'
           'Câu hỏi kiểu "tại sao", "nên làm gì" cần AI phân tích; máy này chưa cấu hình AI nên em chưa trả lời được phần đó.')
THANKS = 'Dạ, có gì anh cứ nhắn em nhé.'
REFUSE = 'Dạ, phần số liệu này em chỉ gửi cho owner thôi ạ. Anh/chị cần thì nhờ owner hỏi giúp nhé.'
GIUGIA = ('Dạ, xác nhận giữ nguyên giá là việc thay đổi chính sách nên em không tự làm từ tin nhắn thường. '
          'Anh chắc chắn thì gửi đúng lệnh /giugia giúp em nhé.')
