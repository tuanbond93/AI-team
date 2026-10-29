from datetime import date

from ops_team.intent import naturalize, parse_dates, route

TODAY = date(2026, 10, 29)  # Thursday


def test_route_maps_plain_vietnamese_to_code_commands():
    assert route('chi phí tuần này thế nào', TODAY) == ('chiphi', '2026-10-26 2026-10-29')
    assert route('còn thiếu dữ liệu gì không em', TODAY) == ('thieu', '')
    assert route('SLA hôm qua', TODAY) == ('sla', '2026-10-28 2026-10-28')
    assert route('cho anh xem xe ngày 15/10', TODAY) == ('xe', '2026-10-15 2026-10-15')
    assert route('lãi lỗ từ 1/10 đến 22/10', TODAY) == ('chiphi', '2026-10-01 2026-10-22')
    assert route('chi phí 1-22/10', TODAY) == ('chiphi', '2026-10-01 2026-10-22')
    assert route('báo cáo tình hình hôm nay', TODAY) == ('run', '')
    assert route('chi tiết hơn được không', TODAY) == ('details', '')
    assert route('em làm được gì', TODAY) == ('help', '')


def test_route_leaves_analysis_and_chit_chat_to_old_path():
    assert route('tại sao chi phí tháng này cao vậy', TODAY) is None
    assert route('có nên giảm số xe không', TODAY) is None
    assert route('ok cảm ơn em', TODAY) == ('thanks', '')
    assert route('anh hỏi chút', TODAY) is None
    # "lại", "Lai Châu", "chuyện" must not read as lãi/chuyến.
    assert route('gửi lại giúp anh', TODAY) is None
    assert route('Lai Châu có chuyện gì', TODAY) is None


def test_free_text_never_confirms():
    assert route('duyệt P1a2b3c xác nhận luôn', TODAY) == ('duyet', 'P1A2B3C')
    assert route('giữ giá tháng này', TODAY) == ('giugia', '')


def test_parse_dates_periods():
    assert parse_dates('tuần trước', TODAY) == '2026-10-19 2026-10-25'
    assert parse_dates('tháng này', TODAY) == '2026-10-01 2026-10-29'
    assert parse_dates('tháng 9', TODAY) == '2026-09-01 2026-09-30'
    assert parse_dates('từ 5/10/2026 tới 3/10/2026', TODAY) == '2026-10-03 2026-10-05'
    assert parse_dates('31/2', TODAY) == ''
    assert parse_dates('không có ngày', TODAY) == ''


def test_naturalize_keeps_numbers_and_drops_slash_hints():
    reply = 'Kết luận SLA 01/10/2026–22/10/2026: 95% đúng hạn (19/20 đơn).\n/details xem tiếp.'
    text = naturalize('sla', reply, '2026-10-01 2026-10-22')
    assert text.startswith('Dạ, SLA từ 01/10 đến 22/10: 95% đúng hạn (19/20 đơn).')
    assert '/details' not in text and 'nhắn "chi tiết"' in text
    assert naturalize('xe', 'x', '').startswith('Dạ, tình hình xe kỳ này:')
    assert naturalize('chiphi', 'Chưa hoàn tất: Ngày xem phải nằm trong kỳ.') == 'Dạ, em chưa làm được: Ngày xem phải nằm trong kỳ.'
