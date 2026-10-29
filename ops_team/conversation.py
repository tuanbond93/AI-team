"""Understand a plain message before answering, then reply in the SOUL.md persona.

Pipeline (no extra model call to classify; regex on de-accented text, ~1 ms):
  1. memory acts   — "nhớ giúp…", "quên mục 2", "em nhớ gì về anh", "gọi tôi là chị"
  2. identity      — "em là người hay máy"
  3. correction    — "không, ý anh là tuần trước" -> re-run with the fix
  4. work          — intent.route(): /chiphi, /thieu, /sla, /xe, /run, /details, /duyet (preview)
  5. follow-up     — "còn hôm qua thì sao" -> last work command, new dates
  6. complaint / frustration / pushback — own the mistake or keep the evidence, briefly
  7. recall        — "hôm trước em bảo gì" -> only stored episodes, never invented
  8. chit-chat     — greeting, thanks, jokes, tired, small talk
  9. analysis      — returns None: the existing Chief/AI path (code metrics) answers
 10. unclear       — model chat if configured, else a short honest reply

Free text never changes data, prices, schedules or permissions.
"""
from datetime import datetime, timedelta
import re
import time

from . import intent, persona
from .conv_memory import ConversationMemory, is_business_rule, scope_of
from .intent import _plain

LABELS = {'chiphi': 'chi phí', 'thieu': 'danh sách dữ liệu thiếu', 'sla': 'SLA', 'xe': 'tình hình xe', 'run': 'báo cáo',
          'details': 'chi tiết', 'duyet': 'danh sách chờ duyệt'}
WORK = set(LABELS)

P = {
    'memory_show': r'^(em|ban|bot|may) (dang )?(nho|luu|biet) (gi|nhung gi|nhung dieu gi|nhung thong tin gi)( ve (anh|chi|minh|toi|em))?|^(xem|liet ke) (tri nho|nhung gi (em|ban) nho)',
    'memory_forget_id': r'^(em |ban )?(hay )?(quen|xoa) (di )?(giup )?(muc|so|cai|dong)? ?(so )?#?(?P<id>\d{1,6})\b',
    'memory_forget_all': r'^(em |ban )?(hay )?(quen|xoa) (het|tat ca|sach)',
    'memory_forget_text': r'^(em |ban )?(hay )?(quen|dung nho|xoa) (di )?(giup )?(cai|chuyen|dieu|thong tin|viec) (?P<v>.+)',
    'memory_save': r'^(em |ban |bot )?(hay )?(nho|ghi nho|luu lai|ghi lai|note lai)( giup| ho)?( (anh|chi|minh|toi|em))?( la| rang| nhe| nha)?\s*[:,]?\s+(?P<v>.+)',
    'addr': r'\b(goi|xung) (toi|anh|chi|minh|em|tao) (la|bang) (anh|chi|ban)\b|\b(goi|xung ho) (la|bang) (anh|chi|ban)( thoi| nhe| nha)?$',
    'length': r'\btra loi (ngan|ngan gon|it|gon)( thoi| lai| hon)?\b|\b(noi|viet) (ngan|gon) (thoi|lai|hon)\b|\btra loi (dai|chi tiet|ky) (hon|luon)\b',
    'identity': r'\b(em|ban|may|bot) (la|co phai la) (nguoi|ai|robot|bot|may|nguoi that)\b|\b(nguoi|ai) that (hay|khong)\b|\bco phai (nguoi|bot|robot) (khong|ko)\b',
    'correction': r'^(khong,?|khong phai,?|nham,?|nham roi,?|sai roi,?|sua lai,?|a khong,?|y (anh|chi|minh|toi|em) la|ban nay)\s+(?P<v>.+)',
    'complaint': r'\b(sai roi|sai bet|sai het|sai so|nham roi|tinh sai|lam sai|bao sai|gui sai|khong dung|chua dung|lech roi|khong khop)\b',
    'frustrated': r'\b(buc|buc minh|chan qua|chan that|te qua|vo dung|lam an kieu gi|cham qua|lau qua|kem qua|met moi qua|bo tay)\b',
    'pushback': r'^(nhung|sao lai|chac khong|chac chua|chac chan khong|em chac|ban chac|co chac|vo ly|la sao|khong the nao)\b|\b(nghi khac|khong dong y|khong tin)\b',
    'recall': r'\b(hom truoc|hom kia|lan truoc|may hom truoc|hoi nay|luc nay|bua truoc|vua nay|hom qua|tuan truoc)\b.*\b(noi|bao|gui|nhac|tinh|ket qua|thay|ra)\b|\b(em|ban) (da|vua|co) (noi|bao|gui|nhac) (gi|nhung gi|cai gi)\b',
    'recall_strong': r'\b(hom truoc|hom kia|lan truoc|may hom truoc|hoi nay|luc nay|bua truoc|vua nay|vua roi|hom qua)\b.*\b(em|ban|bot) (da |vua |co )?(noi|bao|nhac|gui|tra loi)\b|\b(em|ban|bot) (da |vua |co )?(noi|bao|nhac|gui)\b.*\b(hom truoc|hom kia|lan truoc|may hom truoc|hoi nay|luc nay|bua truoc|vua nay|hom qua)\b',
    'followup': r'^(con|the con|vay con|roi con|the|vay)\b|\b(thi sao|thi the nao|nua khong|nua thi sao)\s*\??$',
    'greeting': r'^(hi|hello|alo|chao|xin chao|e oi|em oi|bot oi|ban oi)\b',
    'thanks': r'\b(cam on|tot lam|good job|gioi qua|hay qua)\b',
    'ack': r'^(ok|vang|uh|u|da|dc|duoc|roi|duoc roi|oki|chuan|nhat tri|dong y)( em| nhe| nha| roi| ban)*\s*[.!]*$',
    'joke': r'haha|hihi|hehe|kkk|=\)\)|:\)\)|:d\b|\blol\b|\bvl\b|buon cuoi',
    'tired': r'\b(met qua|met ghe|met vai|duoi qua|buon ngu|oai qua|cang qua|ngap viec)\b',
    'how_are_you': r'\b(khoe khong|the nao roi|on khong|sao roi)\b.*\b(em|ban|bot)\b|\b(em|ban|bot) (khoe|on|the nao) (khong|chu)\b',
}


def act_of(t, name):
    return re.search(P[name], t)


class Conversation:
    """One per responder. Working memory in RAM; durable memory in the vault."""

    def __init__(self, responder, clock=time.time):
        self.r, self.clock = responder, clock
        self.memory = ConversationMemory(responder.state, clock=clock)
        self.work = {}
        self._chat_agent = None

    # ---------- working memory ----------
    def _wm(self, key):
        if key not in self.work:
            self.work[key] = {'style': None, 'last_work': None, 'recent': [], 'turns': [], 'n': 0, 'hinted': False}
            if len(self.work) > 100:
                self.work.pop(next(iter(self.work)))
        return self.work[key]

    def _remember_turn(self, wm, text, reply, opener):
        wm['n'] += 1
        wm['recent'] = [*wm['recent'], opener][-3:]
        wm['turns'] = [*wm['turns'], {'user': text[:200], 'assistant': reply[:300]}][-6:]

    # ---------- entry ----------
    def handle(self, request):
        """Reply text, or None when the old path (onboarding, Chief/AI analysis) should answer."""
        owner = request.user_id == self.r.owner
        if owner and self.r.state.control('pending_field') and not self.r._facts().get('source_id'):
            return None
        key = (request.chat_id, request.topic_id, request.user_id)
        scope = scope_of(request.chat_id, request.user_id)
        wm = self._wm(key)
        style = persona.detect_style(request.text, self.memory.preferences(scope), wm['style'])
        wm['style'] = style
        seed = wm['n']+request.message_id
        reply, opener = self._dispatch(request, owner, scope, wm, style, seed)
        if reply is None:
            wm['n'] += 1
            return None
        self._remember_turn(wm, request.text, reply, opener)
        return reply

    def _say(self, kind, wm, style, seed, **values):
        return persona.chat(kind, style, seed, wm['recent'], **values), kind+':'+str(seed % 7)

    def _dispatch(self, request, owner, scope, wm, style, seed):
        text = request.text.strip()
        t = _plain(text)
        today = datetime.fromtimestamp(self.clock(), self.r_tz()).date()
        say = lambda kind, **v: self._say(kind, wm, style, seed, **v)

        # 1. memory
        if act_of(t, 'memory_show'):
            return self.show(scope, style), 'memory'
        if m := act_of(t, 'memory_forget_id'):
            n = self.memory.forget(scope, int(m['id']))
            return say('memory_forgot', n=n) if n else say('memory_forget_none')
        if act_of(t, 'memory_forget_all'):
            return say('memory_forget_all')
        if m := act_of(t, 'addr'):
            addr = {'anh': 'anh', 'chi': 'chị', 'ban': 'bạn'}[m[4] or m[7]]
            self.memory.add(scope, 'preference', addr, self._source(request), key='addr')
            style.addr, style.me = addr, ('mình' if addr == 'bạn' else 'em')
            return say('memory_saved', value='gọi là '+addr)
        if m := act_of(t, 'length'):
            value = 'dài' if re.search(r'dai|chi tiet|ky', m[0]) else 'ngắn'
            self.memory.add(scope, 'preference', value, self._source(request), key='length')
            style.brief = value == 'ngắn'
            return say('memory_saved', value='trả lời '+value)
        if m := act_of(t, 'memory_forget_text'):
            hits = self.memory.relevant(scope, m['v'], limit=1, kinds=('semantic', 'project'))
            if not hits:
                return say('memory_forget_none')
            return say('memory_forgot', n=self.memory.forget(scope, hits[0]['id']))
        if m := act_of(t, 'memory_save'):
            return self.save(request, scope, wm, style, seed, tail_after(text, t, m.start('v')))

        # 2. identity
        if act_of(t, 'identity'):
            return say('identity')

        # 3. correction -> redo with the fix
        if m := act_of(t, 'correction'):
            rest = tail_after(text, t, m.start('v'))
            hit = intent.route(rest, today)
            dates = intent.parse_dates(rest, today)
            if hit and hit[0] in WORK:
                return self.run_work(request, owner, scope, wm, style, seed, *hit, prefix='À, ')
            if dates and wm['last_work']:
                return self.run_work(request, owner, scope, wm, style, seed, wm['last_work'][0], dates, prefix='À, ')

        # 4a. clear recall / pushback beat keyword routing ("hôm trước em bảo thiếu gì")
        if act_of(t, 'recall_strong'):
            return self.recall(scope, text, style, seed, wm, today)
        if wm['last_work'] and re.match(P['pushback'], t) and (not intent.parse_dates(text, today) or re.search(r'\b(the duoc|lam gi co|vo ly|khong the|chac khong|chac chua)\b', t)):
            return say('pushback_work', last=self._last_text(wm))

        # 4. work
        hit = intent.route(text, today)
        if hit:
            command, args = hit
            if command == 'help' and act_of(t, 'greeting') and len(t.split()) <= 4:
                return say('greeting')
            if command == 'thanks' and not re.search(r'cam on', t):
                return say('ack')
            if command == 'help':
                return intent.HELP if style.addr == 'anh' else intent.HELP.replace('anh cứ', style.addr+' cứ'), 'help'
            if command == 'thanks':
                return say('thanks')
            return self.run_work(request, owner, scope, wm, style, seed, command, args)

        # 5. follow-up on the last work topic
        dates = intent.parse_dates(text, today)
        if wm['last_work'] and dates and (act_of(t, 'followup') or len(t.split()) <= 6):
            return self.run_work(request, owner, scope, wm, style, seed, wm['last_work'][0], dates)

        # 6. complaint / frustration / pushback
        last = self._last_text(wm)
        if act_of(t, 'complaint'):
            return say('complaint_work', last=last) if last else say('frustrated')
        if act_of(t, 'frustrated'):
            return say('frustrated')
        if act_of(t, 'pushback'):
            if last:
                return say('pushback_work', last=last)
            return self.model_or('pushback', request, scope, wm, style, seed)

        # 7. recall
        if act_of(t, 'recall'):
            return self.recall(scope, text, style, seed, wm, today)

        # 8. chit-chat
        for kind in ('greeting', 'ack', 'joke', 'tired', 'how_are_you'):
            if act_of(t, kind):
                if kind in ('greeting', 'ack'):
                    return say(kind)
                return self.model_or(kind, request, scope, wm, style, seed)

        # 9. analysis -> existing Chief/AI path with code metrics
        from .chief.agent import is_evaluation
        if any(re.search(p, t) for p in intent.ANALYSIS) or is_evaluation(text):
            return None, None

        # 10. unclear / small talk
        return self.model_or('unclear' if len(t.split()) <= 3 or re.search(r'\b(xem|tinh|kiem tra|so lieu|bao nhieu)\b', t) else 'smalltalk',
                             request, scope, wm, style, seed)

    def r_tz(self):
        from .telegram_assistant import TZ
        return TZ

    @staticmethod
    def _source(request):
        return f'telegram:{request.chat_id}:{request.message_id}'

    def _last_text(self, wm):
        if not wm['last_work']:
            return ''
        command, args = wm['last_work']
        return LABELS[command]+' '+intent.span_text(args)

    # ---------- work ----------
    def run_work(self, request, owner, scope, wm, style, seed, command, args, prefix=''):
        say = lambda kind, **v: self._say(kind, wm, style, seed, **v)
        if not owner:
            return say('refuse')
        if command == 'giugia':
            return say('giugia')
        from .telegram_bridge import Incoming
        reply = self.r._answer(Incoming(request.chat_id, request.user_id, request.message_id, request.topic_id, command, args))
        hint = not style.brief and not wm.get('hinted')
        text, opener = persona.frame(command, reply, args, style, seed, wm['recent'], hint=hint)
        if 'nhắn "chi tiết"' in text or 'nhắn "còn thiếu gì"' in text:
            wm['hinted'] = True
        if opener != 'error':
            wm['last_work'] = (command, args)
            head = text.split('\n', 1)[0].rstrip(':')[:200]
            if command in ('chiphi', 'thieu', 'sla', 'xe', 'run'):
                self.memory.add(scope, 'episodic', head, 'code:'+command)
        if prefix:
            text = prefix+persona.lower_first(text.removeprefix('Dạ, '))
        return text, opener

    # ---------- memory ----------
    def save(self, request, scope, wm, style, seed, value):
        say = lambda kind, **v: self._say(kind, wm, style, seed, **v)
        value = value.strip(' .!')
        from .conv_memory import check_storable
        if reason := check_storable(value):
            return say('memory_refused', reason=reason)
        if is_business_rule(value):
            return say('memory_rule')
        try:
            self.memory.add(scope, 'project' if re.search(r'\b(du an|project|kho|tuyen|ncc|khach hang)\b', _plain(value)) else 'semantic',
                            value, self._source(request))
        except ValueError as exc:
            return say('memory_refused', reason=str(exc))
        return say('memory_saved', value=value)

    def show(self, scope, style):
        rows = self.memory.listing(scope)
        if not rows:
            return persona.chat('memory_empty', style, 0)
        names = {'preference': 'cách nói', 'semantic': 'ghi nhớ', 'project': 'dự án', 'episodic': 'lần xem số'}
        shown = lambda r: {'addr': 'gọi là ', 'length': 'trả lời '}.get(r['key'], '')+r['value'] if r['kind'] == 'preference' else r['value']
        lines = [f"#{r['id']} ({datetime.fromtimestamp(r['created'], self.r_tz()):%d/%m}, {names[r['kind']]}) {shown(r)}" for r in rows]
        return (f"{persona.cap(style.me)} đang nhớ những điều này về {style.addr}:\n"+'\n'.join(lines)+
                f'\nMuốn bỏ mục nào thì nhắn "quên mục <số>", hoặc /quen all để xóa hết.')

    def recall(self, scope, text, style, seed, wm, today):
        hits = self.memory.relevant(scope, text, limit=3)
        if not hits:
            days = 2 if 'hom qua' in _plain(text) or 'vua nay' in _plain(text) or 'hoi nay' in _plain(text) else 7
            hits = self.memory.episodes(scope, since=self.clock()-days*86400)[-3:]
        if not hits:
            return self._say('recall_none', wm, style, seed)
        lines = []
        for row in hits:
            when = datetime.fromtimestamp(row['created'], self.r_tz())
            day = 'hôm nay' if when.date() == today else 'hôm qua' if when.date() == today-timedelta(days=1) else when.strftime('%d/%m')
            said = f'{style.me} gửi' if row['kind'] == 'episodic' else f'{style.addr} bảo {style.me} nhớ'
            lines.append(f'{persona.cap(day) if day[0].isalpha() else "Hôm "+day} {said}: {row["value"]}')
        head = 'Theo ghi chép của '+style.me+':' if len(lines) > 1 else ''
        return ((head+'\n' if head else '')+'\n'.join(lines)), 'recall'

    # ---------- model chat ----------
    def model_or(self, kind, request, scope, wm, style, seed):
        """Open chat goes to the model when configured; otherwise an honest persona reply."""
        if self.r.settings.get('models'):
            try:
                reply = self._model_chat(request, scope, wm, style)
                return reply, 'model'
            except Exception:
                pass
        fallback = {'pushback': 'pushback', 'unclear': 'unclear', 'smalltalk': 'smalltalk'}.get(kind, kind)
        return self._say(fallback, wm, style, seed)

    def brief(self, request, scope, wm, style):
        memory = [{'id': r['id'], 'kind': r['kind'], 'value': r['value'],
                   'date': datetime.fromtimestamp(r['created'], self.r_tz()).strftime('%Y-%m-%d')}
                  for r in self.memory.relevant(scope, request.text, limit=3)]
        return {'persona': persona.soul_text(), 'style': style.to_dict(), 'message': request.text[:1000],
                'turns': [{'user': x['user'][:160], 'assistant': x['assistant'][:240]} for x in wm['turns'][-3:]],
                'memory': memory, 'work': self._last_text(wm) or None}

    def _model_chat(self, request, scope, wm, style):
        from .providers import ModelConfig
        from .telegram_agent import TelegramAgent
        if self._chat_agent is None:
            configs = {n: ModelConfig(**c) for n, c in self.r.settings.get('models', {}).items()}
            self._chat_agent = TelegramAgent(configs)
        reply = self._chat_agent.chat(self.brief(request, scope, wm, style))
        # Chat replies carry no business figures: drop sentences with unsourced quantities.
        from .telegram_presentation import guarded_no_source_text
        safe, blocked = guarded_no_source_text(reply)
        if not safe:
            raise ValueError('model reply removed by guard')
        return safe


def tail_after(text, plain_text, start):
    """Original words after the first ``start`` chars of the de-accented text (slang may expand one word to two)."""
    skip = len(plain_text[:start].split())
    words, used = text.split(), 0
    for i, word in enumerate(words):
        if used >= skip:
            return ' '.join(words[i:])
        used += len(_plain(word).split())
    return ''
