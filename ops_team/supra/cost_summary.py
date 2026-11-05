"""The /chiphi cost table, reused by the 13h report and the model context.

Reads a ``manual_cost_report`` result and never recalculates anything, so the daily
report, the model and /chiphi show the same numbers for the same scope. The report
scope is the reporting period from day 1 to the cutoff; when the approved cost policy
does not cover that whole scope, cost stays N/A with the reason (same rule as DC
handling: OWNER_RULE_WINDOW_NOT_COVERING_SCOPE), never a silently narrower scope.
"""
from calendar import monthrange
from datetime import date

REASONS = {
    'NOT_CONFIGURED': None,  # no cost policy loaded: keep the legacy report line
    'RANGE_REQUIRED': 'chưa xác định được khoảng ngày của kỳ',
    'OUTSIDE_POLICY_WINDOW': 'chính sách chi phí đã duyệt không phủ đủ từ đầu kỳ tới ngày chốt; owner cần nạp chính sách cho cả kỳ',
    'OUTSIDE_RULES_WINDOW': 'quy tắc owner không phủ đủ từ đầu kỳ tới ngày chốt',
    'POLICY_OR_SOURCE_MISMATCH': 'chính sách chi phí không còn khớp bản nguồn hiện tại; cần nạp lại cho đúng bản nguồn',
}


def period_scope(period, cutoff):
    """'YYYY-MM-01 YYYY-MM-DD' from the first day of the period to the cutoff (or month end)."""
    year, month = int(period[:4]), int(period[5:7])
    end = min(date(year, month, monthrange(year, month)[1]), cutoff.date())
    return f'{period}-01 {end}'


def money(value):
    from .vehicle_report import _vi_money
    return _vi_money(value)+'đ' if value is not None else 'chưa đủ căn cứ'


def cost_lines(report, scope_text, show_amounts=False):
    """Lines for the 13h report, or None to keep the legacy line (no cost policy loaded).

    The report goes to the group: by default it states the same status as /chiphi (total
    available or N/A with the same reasons) without new money figures; the owner reads
    the amounts with /chiphi. ``show_amounts`` prints the exact /chiphi sentence (OWNER J9).
    """
    from .missing_data import _vi_day
    start, end = scope_text.split()
    span = _vi_day(start)+'–'+_vi_day(end)
    if not report.get('available'):
        reason = REASONS.get(report.get('reason'), 'chính sách hoặc nguồn chưa đối chiếu được')
        if reason is None:
            return None
        return [f'Chi phí {span} (cùng bảng /chiphi): N/A vì {reason}.']
    from .manual_cost_overlay import projected_totals
    from .manual_cost_report import _projected_line
    total, _, blockers = projected_totals(report)
    head = f'Chi phí {span} (cùng bảng /chiphi): '
    if show_amounts and (report.get('overlay') or {}).get('available'):
        return [head+_projected_line(report, money)]
    if total is not None:
        return [head+'đã đủ nguồn để có tổng chi phí và lãi/lỗ dự kiến trước VAT. Owner xem số bằng /chiphi.']
    return [head+'chưa có tổng chi phí/lãi lỗ dự kiến vì '+'; '.join(blockers[:3])+('…' if len(blockers) > 3 else '')+
            '. Owner xem phần đã tính bằng /chiphi.']


def cost_context(report, scope_text):
    """Compact, code-computed cost facts for the model. Values are copied from the report as strings."""
    from .manual_cost_overlay import projected_totals
    if not report.get('available'):
        return {'available': False, 'scope': scope_text.split(), 'reason': report.get('reason')}
    p, f, g, w = report['points'], report['ftl'], report['gxt'], report['warehouse']
    overlay = report.get('overlay') or {}
    total, gp, blockers = projected_totals(report)
    return {'available': True, 'scope': scope_text.split(), 'source': 'manual_cost_report (/chiphi)',
            'points_vnd': p.get('eligible_vnd'), 'remote_ncc_vnd': (report.get('remote') or {}).get('eligible_vnd'),
            'ftl_vnd': f.get('eligible_vnd'), 'gxt_vehicle_supra_vnd': g.get('vehicle_supra_vnd'), 'gxt_wage_vnd': g.get('wage_supra_vnd'),
            'dc_incremental_vnd': report.get('dc_incremental_vnd'), 'gxt_warehouse_pool_vnd': w.get('shared_pool_vnd'),
            'blank_points_vnd': (report.get('blank_points') or {}).get('eligible_vnd'),
            'revenue_vnd': (overlay.get('revenue') or {}).get('eligible_vnd') if overlay.get('available') else None,
            'known_cost_subtotal_vnd': overlay.get('known_cost_subtotal') if overlay.get('available') else None,
            'projected_total_vnd': total, 'projected_gross_profit_vnd': gp, 'blockers': blockers,
            'held_groups': {'points': len(p.get('held', [])), 'ftl': len(f.get('held', [])), 'gxt': len(g.get('held', []))},
            'policy_sha256': report.get('policy_sha256'), 'rules_sha256': report.get('rules_sha256'),
            'semantics': 'Code-computed. A None value is N/A (missing source), never 0. known_cost_subtotal is partial, not a total. '
                         'projected_total exists only when every in-scope part is complete. Before VAT; not settled cost.'}
