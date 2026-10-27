"""Owner-confirmed product units. No carton/parcel conversion or financial math."""
from collections import Counter, defaultdict
import math
import re

SHEET_ID = '1fifr6Wacb5zMxWiWgM-HH9xl_z2Px9bGJ4mJk1twHiY'
BUSINESS_HEADERS = ['Ngày LTC', 'Mã đơn GHN', 'Trạng thái', 'Ngày xuất', 'MÃ SO', 'MÃ DO', 'Qty (Units)', 'Tỉnh']
QUANTITY_COLUMNS = ['source_so', 'source_do', 'source_product_units', 'source_province']
RULE = {
    'version': 'supra-product-units-owner-2026-10-26-v1',
    'provenance': 'USER_CONFIRMED_IN_CODEX', 'date': '2026-10-26',
    'ghn_column': 'B', 'product_units_column': 'J',
    'product_unit_definition': 'PRODUCT_UNIT_INSIDE_CARTON_NOT_PARCEL_OR_CARTON',
    'aggregation': 'SUM_PRODUCT_UNITS_OF_DISTINCT_DO_LINES_BY_GHN',
    'business_key_columns': ['B', 'F', 'G'],
    'repeated_key_policy': 'UNRESOLVED_NO_SILENT_DOUBLE_COUNT_OR_DELETION',
    'telegram_message_id': None, 'observed_source_not_independently_reconciled': True,
}


def owner_quantity_rule(source):
    if (source.get('spreadsheet_id') == SHEET_ID and source.get('tab') == 'DC PHÚ THỌ'
            and str(source.get('gid')) == '0'):
        return dict(RULE)
    return None


def export_quantity_rule(headers, source):
    rule=owner_quantity_rule(source)
    if rule:
        from .booking import verify_export_headers
        # Legacy/partial connectors still work, but do not invent J mapping.
        if len(headers) != 37:
            return None
        verify_export_headers(headers)
    return rule


def cell(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ''
    return str(value).strip()


def business_scalar(value):
    """JSON-safe source evidence, preserving missing versus invalid versus zero."""
    if hasattr(value,'item'):
        value=value.item()
    if isinstance(value,float) and not math.isfinite(value):
        return None if math.isnan(value) else str(value)
    return value


def product_units(value):
    """Only unambiguous, nonnegative integers. Never coerce blank/error to zero."""
    if value is None or cell(value) == '':
        return None, 'MISSING'
    if isinstance(value, bool):
        return None, 'INVALID'
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer() and 0 <= value <= 2**53-1:
            return int(value), 'VALID'
        return None, 'INVALID'
    text = cell(value)
    if re.fullmatch(r'[0-9]{1,15}', text):
        return int(text), 'VALID'
    return None, 'INVALID'


def business_key(value):
    text=cell(value)
    if text.casefold() in ('nan','none','null','n/a','na','-'):
        return ''
    return text if re.fullmatch(r'[A-Za-z0-9_./-]{1,120}',text) else ''


def quantity_summary(data, scoped):
    """Inspect the whole pinned source for key collisions; sum only scoped DOs.

    Multiple dispatch dates across DISTINCT DOs are valid partial shipments.
    Every row and evidence stays in the vault. No silent dedup/last-row choice.
    """
    rules = getattr(data, 'quantity_rules', None)
    available = bool(rules == RULE and all(c in data.rows for c in QUANTITY_COLUMNS))
    result = dict(available=available, rule=rules if available else None,
                  complete=False, product_units=None, eligible_product_units=None,
                  missing_quantity_rows=0, invalid_quantity_rows=0, missing_key_rows=0,
                  repeated_key_rows=0, conflicting_key_rows=0, quarantined_rows=0,
                  eligible_rows=0, eligible_ghn=0, scoped_rows=len(scoped),
                  source_row_count=len(data.rows), by_ghn=[], issue_sources=[])
    if not available:
        result['reason'] = 'MISSING_CONFIRMED_MAPPING_OR_QUANTITY_FIELDS'
        return result
    keys, owners = Counter(), defaultdict(set)
    for row in data.rows.to_dict('records'):
        ghn, so, do = cell(row['order_id']), business_key(row['source_so']), business_key(row['source_do'])
        if ghn and so and do:
            keys[(ghn,so,do)] += 1
            owners[(so,do)].add(ghn)
    rows=scoped.to_dict('records'); bad_ghn=set(); row_reasons=[]
    for r in rows:
        ghn, so, do = cell(r['order_id']), business_key(r['source_so']), business_key(r['source_do'])
        value, status=product_units(r['source_product_units'])
        reasons=[]
        if status != 'VALID':
            result['missing_quantity_rows' if status=='MISSING' else 'invalid_quantity_rows'] += 1
            reasons.append(status+'_QUANTITY')
        if not (ghn and so and do):
            result['missing_key_rows'] += 1; reasons.append('MISSING_BUSINESS_KEY')
        elif keys[(ghn,so,do)] > 1:
            result['repeated_key_rows'] += 1; reasons.append('REPEATED_GHN_SO_DO')
        if so and do and len(owners[(so,do)]) > 1:
            # Owner only confirmed the GHN+SO+DO grain. Different GHNs for one
            # SO/DO are a review warning, not permission to discard shipments.
            result['conflicting_key_rows'] += 1
        if reasons:
            bad_ghn.add(ghn)
        row_reasons.append((r,value,reasons))
    by_ghn=defaultdict(lambda: dict(product_units=0,do_lines=[]))
    for r,value,reasons in row_reasons:
        # A missing/ambiguous row prevents presenting a partial GHN as complete.
        if reasons or r['order_id'] in bad_ghn:
            result['quarantined_rows'] += 1
            result['issue_sources'].append(r['_source'])
            continue
        entry=by_ghn[r['order_id']]
        entry['product_units'] += value
        entry['do_lines'].append(dict(source_so=r['source_so'],source_do=r['source_do'],
            dispatch_date=r['dispatch_date'],province=r['source_province'],product_units=value,source=r['_source']))
        result['eligible_rows'] += 1
    result['by_ghn']=[dict(order_id=k,**v) for k,v in sorted(by_ghn.items())]
    result['eligible_ghn']=len(by_ghn)
    result['eligible_product_units']=sum(v['product_units'] for v in by_ghn.values()) if by_ghn else None
    result['complete']=bool(rows) and not result['quarantined_rows']
    result['product_units']=result['eligible_product_units'] if result['complete'] else None
    result['reason']='COMPLETE_SOURCE_DECLARATION' if result['complete'] else 'PARTIAL_KEYS_OR_QUANTITY_UNRESOLVED'
    return result
