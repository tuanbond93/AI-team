"""Persona rules from SOUL.md applied in code: address forms, register, varied wording.

Used for every reply when no model is configured, and for framing code results
(numbers are never touched). Variation is deterministic per turn so tests are
stable, and the same opener is not reused within the last few turns.
"""
from dataclasses import dataclass
from pathlib import Path
import re

from .intent import _plain, span_text

SOUL_PATH = Path(__file__).resolve().parents[1]/'SOUL.md'


def soul_text(limit=3000):
    """Bullet rules of SOUL.md (headings/intro dropped to save tokens); built-in fallback if missing."""
    try:
        rules = [line[2:].strip() for line in SOUL_PATH.read_text(encoding='utf-8').splitlines() if line.startswith('- ')]
        return ('SOUL: '+' '.join(rules))[:limit]
    except OSError:
        return ('Trợ lý AI của đội vận hành; nói thật mình là AI. Tiếng Việt đời thường, thân thiện, thẳng, có chính kiến, '
                'không nịnh, không văn tổng đài, hỏi ngắn trả lời ngắn, không bịa số hay ký ức.')


@dataclass
class Style:
    addr: str = 'anh'        # how the bot calls the user
    me: str = 'em'           # how the bot calls itself
    formal: bool = False     # "Dạ ... ạ" register
    brief: bool = False      # user asked for short answers

    def to_dict(self):
        return {'goi_nguoi_dung': self.addr, 'tu_xung': self.me, 'trang_trong': self.formal, 'ngan_gon': self.brief}


def detect_style(text, prefs=None, previous=None):
    """Saved preference > how the user writes now > previous turn > default anh/em."""
    prefs, t = prefs or {}, _plain(text)
    style = Style(**(previous.__dict__ if previous else {}))
    if re.search(r'\b(chi|chị)\s+(hoi|can|muon|nho|thay|nghi|bao|xem|gui|dang|vua|da|chua|se)\b', t) or re.search(r'\b(cua|cho|giup|voi) chi\b', t):
        style.addr, style.me = 'chị', 'em'
    elif re.search(r'\b(anh)\s+(hoi|can|muon|nho|thay|nghi|bao|xem|gui|dang|vua|da|chua|se)\b', t) or re.search(r'\b(cua|cho|giup|voi) anh\b', t):
        style.addr, style.me = 'anh', 'em'
    elif re.search(r'\b(ban|cau)\b', t) and re.search(r'\b(minh|toi|to)\b', t):
        style.addr, style.me = 'bạn', 'mình'
    raw = str(text).casefold()
    if re.search(r'\b(dạ|ạ|vui lòng|xin phép|kính)\b', raw):
        style.formal = True
    elif re.search(r'\b(ê|ơi|nhé|nha|hả|ha|nhỉ)\b|=\)\)|:\)\)|haha|kkk', raw) or not re.search(r'[à-ỹđ]', raw):
        style.formal = False
    if prefs.get('addr'):
        style.addr = prefs['addr']
        style.me = 'mình' if style.addr == 'bạn' else 'em'
    if prefs.get('length') in ('ngắn', 'dài'):
        style.brief = prefs['length'] == 'ngắn'
    return style


def cap(text):
    return text[:1].upper()+text[1:] if text else text


def pick(options, seed, recent=()):
    """Deterministic choice that avoids options used in the last turns."""
    fresh = [o for o in options if o not in recent] or list(options)
    return fresh[seed % len(fresh)]


def fill(template, style, **extra):
    return template.format(addr=style.addr, Addr=cap(style.addr), me=style.me, Me=cap(style.me), **extra)


def politely(text, style):
    """Formal register adds 'Dạ' once; casual stays plain."""
    if style.formal and not text.startswith('Dạ'):
        return 'Dạ, '+text[:1].lower()+text[1:]
    return text


# Openers for code results. "" = go straight to the content.
OPENERS = {
    'chiphi': ['Chi phí {span}: {head}', 'Chi phí {span} em tính ra thế này: {head}', 'Về chi phí {span}, {head}', 'Số chi phí {span} đây {addr}: {head}'],
    'sla': ['SLA {span}: {head}', 'Giao hàng {span}: {head}', 'SLA {span} thế này: {head}'],
    'run': ['Tình hình kỳ này: {head}', '{head_cap}', 'Tóm lại: {head}'],
    'xe': ['Xe {span}:', 'Tình hình xe {span}:', 'Đây là phần xe {span}:'],
    'details': ['Chi tiết đây {addr}:', 'Phần chi tiết:', ''],
    'thieu_some': ['{Span} còn {n} nhóm dữ liệu cần bổ sung, em để trống chứ không tính là 0:',
                   'Còn {n} nhóm thiếu {span}. Mấy chỗ này em để trống, không điền 0:',
                   '{n} nhóm dữ liệu {span} chưa đủ, em chưa tính phần đó:'],
    'thieu_none': ['{Span} không thiếu dòng nào.', 'Dữ liệu {span} đủ rồi, không còn dòng thiếu.', 'Không còn chỗ nào thiếu trong {span}.'],
}

CHAT = {
    'greeting': ['Chào {addr}.', 'Chào {addr}, {me} đây.', '{Addr} cần xem gì cứ nhắn {me}.', 'Có {me} đây.'],
    'thanks': ['Không có gì {addr}.', 'Ok {addr}.', 'Dạ, có gì {addr} cứ nhắn.', 'Vâng.'],
    'ack': ['Ok {addr}.', 'Vâng.', 'Rồi {addr}.'],
    'joke': ['Haha, công nhận.', 'Câu này hay đấy {addr}.', 'Haha.'],
    'tired': ['Cuối ngày dễ đuối thật. Việc gì cần số thì {addr} cứ để {me} xem.', 'Nghỉ tay chút đi {addr}, phần số liệu {me} trông cho.'],
    'how_are_you': ['{Me} vẫn chạy ổn. {Addr} thì sao?', 'Vẫn ổn {addr}, máy chạy đều.'],
    'identity': ['{Me} là trợ lý AI của đội, không phải người. Số liệu {me} lấy từ nguồn và tính bằng code; phần nhận xét thì {me} nói rõ là nhận xét.'],
    'smalltalk': ['Chuyện phiếm thì {me} còn hạn chế vì máy này chưa bật AI trò chuyện. Phần chi phí, xe, SLA, dữ liệu thiếu thì {addr} cứ hỏi.',
                  '{Me} chưa nói chuyện phiếm được nhiều khi chưa bật AI. Còn việc số liệu thì {addr} hỏi thoải mái.'],
    'frustrated': ['{Me} hiểu là đang bực. {Addr} nói {me} nghe việc nào chưa ổn, {me} xem đúng chỗ đó.',
                   'Vâng, chưa ổn thì {me} sửa. {Addr} chỉ {me} phần nào sai nhé.'],
    'complaint_work': ['Có thể {me} sai chỗ đó. Số vừa rồi là {last}, tính bằng code từ nguồn. {Addr} chỉ {me} ngày, mã đơn hay con số nào lệch để {me} đối chiếu lại.',
                       'Vâng, để {me} kiểm lại. Phần vừa gửi là {last}. Lệch ở ngày hay mã đơn nào thì {addr} báo {me}, {me} soát lại đúng chỗ đó.'],
    'pushback_work': ['Phần số thì {me} vẫn giữ theo nguồn: {last} là code tính từ Booking/Raw hiện có. Nếu {addr} có số khác, gửi {me} xem lệch ở đâu, có khi nguồn chưa cập nhật.',
                      '{Me} chưa thấy căn cứ để đổi số: {last} lấy thẳng từ nguồn. {Addr} thấy chỗ nào khác thực tế thì chỉ {me}, {me} soát lại dòng đó.'],
    'pushback': ['{Me} có thể sai, nhưng {me} cần biết {addr} thấy chỗ nào chưa đúng để xem lại cho trúng.'],
    'unclear': ['{Me} chưa rõ {addr} muốn xem phần nào. {Me} xem được chi phí/lãi lỗ, dữ liệu còn thiếu, SLA, tình hình xe, ví dụ "chi phí tuần này" hay "SLA hôm qua".',
                'Câu này {me} chưa bắt được ý. {Addr} cần chi phí, xe, SLA hay dữ liệu thiếu? Nói kèm ngày cũng được, kiểu "xe ngày 15/10".'],
    'refuse': ['Phần số liệu này {me} chỉ gửi cho owner. {Addr} cần thì nhờ owner hỏi giúp.'],
    'giugia': ['Giữ nguyên giá là thay đổi chính sách nên {me} không làm từ tin nhắn thường. {Addr} chắc rồi thì gửi đúng lệnh /giugia.'],
    'recall_none': ['{Me} không thấy ghi chép nào khớp chuyện đó nên không đoán. {Addr} nhắc lại giúp {me} là việc gì nhé.',
                    '{Me} không có ghi chú nào về việc đó. {Me} chỉ nhớ những gì {addr} bảo nhớ và các lần xem số liệu, nên không muốn đoán bừa.'],
    'memory_saved': ['Ok, {me} nhớ rồi: {value}.', 'Đã ghi nhớ: {value}.'],
    'memory_refused': ['Cái này {me} không lưu ({reason}). Thông tin kiểu đó {me} chỉ dùng ngay trong lượt này thôi.'],
    'memory_rule': ['Giá, phí hay quy tắc tính thì {me} không lấy từ trí nhớ trò chuyện, sợ lệch với bảng đã duyệt. {Addr} gửi qua bảng/lệnh duyệt như mọi khi nhé.'],
    'memory_empty': ['{Me} chưa lưu gì riêng về {addr}.'],
    'memory_forgot': ['Xong, {me} đã xóa {n} mục.', 'Đã quên {n} mục.'],
    'memory_forget_none': ['{Me} không thấy mục đó trong trí nhớ của {addr}.'],
    'memory_forget_all': ['Xóa hết thì {addr} gửi /quen all giúp {me}, để chắc là không lỡ tay.'],
}


def lower_first(text):
    """Lower the first letter for joining after a prefix, but keep acronyms like SLA."""
    return text[:1].lower()+text[1:] if len(text) > 1 and text[1].islower() else text


def chat(kind, style, seed, recent=(), **values):
    text = fill(pick(CHAT[kind], seed, recent), style, **values)
    return politely(text, style) if kind not in ('joke',) else text


HINT_LINE = re.compile(r'\s*(?:Anh|Chị|Bạn|anh|chị|bạn) (?:cần chi tiết thì nhắn "chi tiết" nhé\.|nhắn "còn thiếu gì"[^.\n]*\.?)')


def frame(command, reply, args, style, seed, recent=(), hint=True):
    """Natural framing around a code reply; returns (text, opener_id). Numbers/rows unchanged.

    hint=False drops the "nhắn 'chi tiết'" pointer (already said earlier in this conversation,
    or the user asked for short answers).
    """
    from .intent import HINTS
    text = reply.strip()
    for pattern, repl in HINTS:
        text = re.sub(pattern, repl, text)
    if not hint:
        text = HINT_LINE.sub('', text)
    if style.addr != 'anh':
        text = text.replace('anh nhắn "còn thiếu gì"', style.addr+' nhắn "còn thiếu gì"').replace('Anh cần chi tiết', cap(style.addr)+' cần chi tiết')
        text = text.replace('Anh nhắn "', cap(style.addr)+' nhắn "')
    if text.startswith('Chưa hoàn tất: '):
        return politely(cap(style.me)+' chưa làm được: '+text[len('Chưa hoàn tất: '):], style), 'error'
    span = span_text(args)
    values = {'span': span, 'Span': cap(span), 'addr': style.addr}
    if command == 'thieu':
        m = re.match(r'CẦN ANH BỔ SUNG(?: — [^:]+)?: (\d+) nhóm dữ liệu\. [^\n]*\n', text)
        if m:
            opener = pick(OPENERS['thieu_some'], seed, recent)
            return politely(opener.format(n=m[1], **values), style)+'\n'+text[m.end():], opener
        if text.startswith(('Dữ liệu', 'Chưa thấy dòng thiếu')):
            opener = pick(OPENERS['thieu_none'], seed, recent)
            return politely(opener.format(**values), style)+' '+text, opener
        return politely(text, style), 'raw'
    head_pattern = {'chiphi': r'^Kết luận: ', 'sla': r'^Kết luận SLA [^:]+: ', 'run': r'^Kết luận: '}.get(command)
    if head_pattern and (m := re.match(head_pattern+r'([^\n]*)', text)):
        head, rest = m[1], text[m.end():]
        opener = pick(OPENERS[command], seed, recent)
        first = opener.format(head=head, head_cap=cap(head), **values)
        if command == 'chiphi':
            rest = re.sub(r'\n\nDỰ KIẾN TRƯỚC VAT [^\n]*', '\n\nPhần dưới là dự kiến trước VAT, chỉ gồm phần đã đủ nguồn.', rest)
            rest = re.sub(r'\n\nCHI PHÍ DỰ KIẾN [^\n]*', '\n\nPhần dưới là chi phí dự kiến, chỉ gồm phần đã đủ nguồn, chưa phải thực chi.', rest)
            rest = rest.replace('TỔNG DỰ KIẾN trước VAT', 'Tổng dự kiến trước VAT')
        if command == 'run':
            rest = rest.replace('CẦN CS BỔ SUNG:', 'Cần CS bổ sung:').replace('CẦN ĐỐI CHIẾU:', 'Cần đối chiếu:').replace('BƯỚC TIẾP:', 'Bước tiếp theo:')
        return politely(first, style)+rest, opener
    if command in ('xe', 'details'):
        opener = pick(OPENERS[command], seed, recent)
        first = opener.format(**values)
        return (politely(first, style)+'\n'+text if first else text), opener
    if command == 'duyet':
        text = text.replace('ĐỀ XUẤT CHỜ ANH DUYỆT', 'Các đợt đang chờ '+style.addr+' duyệt').replace('XEM TRƯỚC — ', 'Xem trước: ')
    return politely(text, style), 'raw'
