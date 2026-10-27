"""Column L is an owner-declared GHN total, never a per-DO quantity."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext
import re
from .booking_quantity import BUSINESS_HEADERS, owner_quantity_rule, export_quantity_rule, cell

WEIGHT_HEADER = 'Weight (kg)/ tổng cửa hàng'
WEIGHT_BUSINESS_HEADERS = BUSINESS_HEADERS[:-1]+[WEIGHT_HEADER,BUSINESS_HEADERS[-1]]
WEIGHT_RULE = {
    'version':'supra-ghn-weight-owner-2026-10-26-v1',
    'provenance':'USER_CONFIRMED_IN_CODEX','date':'2026-10-26',
    'ghn_column':'B','weight_column':'L','weight_unit':'kg',
    'weight_grain':'GHN_TOTAL_ACROSS_DO','aggregation':'COUNT_ONCE_PER_GHN',
    'repeated_same_total_policy':'COUNT_ONCE_PRESERVE_ROWS',
    'conflicting_total_policy':'UNRESOLVED_NO_FIRST_LAST_MAX_SUM',
    'missing_total_policy':'MISSING_NOT_ZERO',
    'scope_policy':'WITHHOLD_IF_ONLY_PART_OF_GHN_DISPATCH_SCOPE',
    'telegram_message_id':None,'observed_source_not_independently_reconciled':True,
}


def owner_weight_rule(source):
    return dict(WEIGHT_RULE) if owner_quantity_rule(source) else None


def export_weight_rule(headers,source):
    return dict(WEIGHT_RULE) if export_quantity_rule(headers,source) else None


def parse_kg(value):
    text=cell(value)
    if not text:
        return None,'MISSING'
    if isinstance(value,bool) or len(text)>100:
        return None,'INVALID'
    # Source convention: dot decimals. A single comma without a dot could be a
    # decimal separator; it is not silently interpreted as a thousands group.
    if ',' in text:
        if (re.fullmatch(r'[1-9][0-9]{0,2}(?:,[0-9]{3})+\.[0-9]+',text)
                or re.fullmatch(r'[1-9][0-9]{0,2}(?:,[0-9]{3}){2,}',text)):
            text=text.replace(',','')
        else:
            return None,'INVALID'
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?',text):
        return None,'INVALID'
    try:
        value=Decimal(text)
        return (value,'VALID') if value.is_finite() and value>=0 else (None,'INVALID')
    except InvalidOperation:
        return None,'INVALID'


def weight_summary(data,scoped,period,cutoff):
    rules=getattr(data,'weight_rules',None)
    available=rules==WEIGHT_RULE and 'source_ghn_weight' in data.rows
    result=dict(available=available,rule=rules if available else None,complete=False,
        weight_kg=None,eligible_weight_kg=None,eligible_ghn=0,missing_ghn=0,
        invalid_ghn=0,conflicting_ghn=0,partial_scope_ghn=0,missing_id_rows=0,
        group_evidence=[],issue_sources=[],metric_sources={})
    if not available:
        result['reason']='MISSING_CONFIRMED_WEIGHT_MAPPING_OR_L'
        return result
    whole=defaultdict(list)
    for r in data.rows.to_dict('records'):
        if r['order_id']:
            whole[r['order_id']].append(r)
    result['missing_id_rows']=int(scoped.order_id.eq('').sum())
    result['metric_sources']['missing_id_rows']=scoped.loc[scoped.order_id.eq(''),'_source'].tolist()
    result['issue_sources']+=result['metric_sources']['missing_id_rows']
    accepted=[]
    for ghn in sorted(set(scoped.order_id)-{''}):
        records=whole[ghn]; refs=[r['_source'] for r in records]
        parsed=[parse_kg(r['source_ghn_weight']) for r in records]
        totals={v for v,s in parsed if s=='VALID'}
        issues=[]
        if any(r['dispatch_date'] is None or r['dispatch_date'].strftime('%Y-%m')!=period or r['dispatch_date']>cutoff.date() for r in records):
            issues.append('partial_scope_ghn')
        if any(s=='INVALID' for v,s in parsed):
            issues.append('invalid_ghn')
        if len(totals)>1:
            issues.append('conflicting_ghn')
        if not totals and all(s=='MISSING' for v,s in parsed):
            issues.append('missing_ghn')
        for key in issues:
            result[key]+=1
            result['metric_sources'].setdefault(key,[]).extend(refs)
        if issues:
            result['issue_sources']+=refs
        else:
            total=next(iter(totals))
            accepted.append(total);result['eligible_ghn']+=1
            result['metric_sources'].setdefault('eligible_ghn',[]).extend(refs)
        result['group_evidence'].append(dict(order_id=ghn,issues=issues,
            source_rows=refs,source_values=[r['source_ghn_weight'] for r in records]))
    with localcontext() as ctx:
        ctx.prec=110  # Sum exact source decimals; never round each DO/group.
        result['eligible_weight_kg']=str(sum(accepted,Decimal(0))) if accepted else None
    result['complete']=bool(len(scoped)) and not result['missing_id_rows'] and not result['issue_sources']
    result['weight_kg']=result['eligible_weight_kg'] if result['complete'] else None
    result['reason']='COMPLETE_SOURCE_DECLARATION' if result['complete'] else 'PARTIAL_GHN_TOTAL_OR_DISPATCH_SCOPE'
    return result
