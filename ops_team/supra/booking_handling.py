"""DC handling component; business rates/routes live only in approved vault data."""
import hashlib
import json
import re
import unicodedata
import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal,localcontext
from .booking_quantity import cell,business_key
from .booking_weight import WEIGHT_RULE,parse_kg

HANDLING_SCHEMA='supra-dc-handling-source-intake-v3'
HANDLING_BUSINESS_HEADERS=['Ngày LTC','Mã đơn GHN','Trạng thái','Ngày xuất','MÃ SO','MÃ DO',
    'Mã siêu thị','Qty (Units)','Weight (kg)/ tổng cửa hàng','Tỉnh','Huyện']
HANDLING_POLICY={
    'version':'supra-dc-handling-policy-v1','provenance':'USER_CONFIRMED_IN_CODEX','date':'2026-10-26',
    'component':'DC_HANDLING','currency':'VND','vehicle_scope':'TRANSFER_ONLY',
    'billing_date_column':'E','weight_column':'L','weight_unit':'kg','weight_grain':'GHN_TOTAL_ACROSS_DO',
    'aggregation':'COUNT_ONCE_PER_ELIGIBLE_GHN','routing_columns':{'province':'N','district':'O','store_id':'H'},
    'routing_precedence':'STORE_BEFORE_DISTRICT',
    'group_policy':'ONE_ROUTING_ONE_RATE_FULL_DISPATCH_SCOPE',
    'unresolved_policy':'NO_SPLIT_FIRST_LAST_MAX_OR_DOUBLE_COUNT',
    'direct_policy':'NOT_APPLICABLE_NO_WEIGHT_REQUIRED',
    'rounding':'EXACT_DECIMAL_NO_ROUNDING_PER_DO_GHN_OR_COMPONENT',
    'verification':'OWNER_TERMS_CALCULATION_NOT_SETTLED_COST',
    'telegram_message_id':None,
}


def period_floor(period):
    """First day of the month 11 months before ``period`` (YYYY-MM)."""
    year,month=int(period[:4]),int(period[5:7])
    index=year*12+month-1-11
    return date(index//12,index%12+1,1)


def catalog_digest(catalog):
    return hashlib.sha256(json.dumps(catalog,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def geography(value,*,province=False):
    text=' '.join(unicodedata.normalize('NFC',cell(value)).casefold().split())
    if province:
        text=re.sub(r'^(?:tỉnh\s+|t\.\s*)','',text)
    else:
        for prefix,pattern in [('tp',r'^(?:thành phố\s+|tp\.?\s+)'),
                               ('tx',r'^(?:thị xã\s+|tx\.?\s+)'),
                               ('h',r'^(?:huyện\s+|h\.\s*)'),('q',r'^(?:quận\s+|q\.\s*)')]:
            if re.match(pattern,text):
                text=prefix+' '+re.sub(pattern,'',text);break
        if text in ('h hạ hoà','hạ hoà'):
            text=text.replace('hoà','hòa')
    return text


def district_name(value):
    """District without its administrative prefix (huyện/thị xã/thành phố/quận) for area rules."""
    return re.sub(r'^(?:h|tx|tp|q) ','',geography(value))


def _bounded_text(value,limit=300,empty=False):
    return isinstance(value,str) and len(value)<=limit and (empty or bool(value.strip())) and not any(ord(c)<32 for c in value)


def validate_catalog(catalog,source,policy=HANDLING_POLICY,expected_digest=None):
    """Structural/source validation only. Never load external business files."""
    try:
        keys={'schema','version','source_pin','period','provenance','confirmed_date','rates','routing','evidence_hashes'}
        if not isinstance(catalog,dict) or set(catalog)!=keys or policy!=HANDLING_POLICY:
            raise ValueError
        if catalog['schema']!='supra-dc-handling-catalog-v1' or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',catalog['version']):
            raise ValueError
        pin=catalog['source_pin']
        if (not isinstance(pin,dict) or set(pin)!={'spreadsheet_id','tab','gid'} or
                any(str(pin[k])!=str(source[k]) for k in pin)):
            raise ValueError
        if catalog['provenance']!='USER_CONFIRMED_IN_CODEX' or not re.fullmatch(r'\d{4}-\d{2}',catalog['period']):
            raise ValueError
        date.fromisoformat(catalog['confirmed_date'])
        lo=date.fromisoformat(catalog['period']+'-01')
        hi=date(lo.year,lo.month,calendar.monthrange(lo.year,lo.month)[1])
        # Month roll-forward (owner 28/10/2026) keeps up to 11 earlier months' intervals unchanged.
        floor=period_floor(catalog['period'])
        def interval(r):
            start,end=date.fromisoformat(r['from']),date.fromisoformat(r['to'])
            if not floor<=start<=end<=hi:raise ValueError
            return start,end
        rates=catalog['rates'];routes=catalog['routing']
        if not isinstance(rates,list) or not 1<=len(rates)<=12 or not isinstance(routes,list) or not 1<=len(routes)<=2000:
            raise ValueError
        seen=set();periods=[]
        for r in rates:
            if (not isinstance(r,dict) or set(r)!={'rate_id','from','to','vnd_per_kg','currency','evidence_ref'} or
                    not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',r['rate_id']) or r['rate_id'] in seen or
                    r['currency']!='VND' or not isinstance(r['vnd_per_kg'],str) or not re.fullmatch(r'[0-9]{1,15}',r['vnd_per_kg']) or
                    not _bounded_text(r['evidence_ref'])):
                raise ValueError
            seen.add(r['rate_id']);a,b=interval(r)
            if any(a<=d and c<=b for c,d in periods):raise ValueError
            periods.append((a,b))
        seen=set();route_intervals=defaultdict(list)
        for r in routes:
            if (not isinstance(r,dict) or set(r)!={'rule_id','scope','province','district','store_id','routing','from','to','evidence_ref'} or
                    not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',r['rule_id']) or r['rule_id'] in seen or
                    r['scope'] not in ('STORE','DISTRICT') or r['routing'] not in ('TRANSFER','DIRECT') or
                    not _bounded_text(r['province'],100) or not _bounded_text(r['district'],100) or
                    not _bounded_text(r['store_id'],120,empty=True) or not _bounded_text(r['evidence_ref'])):
                raise ValueError
            if (r['scope']=='DISTRICT' and r['store_id']) or (r['scope']=='STORE' and business_key(r['store_id'])!=r['store_id']):
                raise ValueError
            if r['scope']=='STORE' and not r['store_id']:raise ValueError
            seen.add(r['rule_id']);a,b=interval(r)
            key=('STORE',r['store_id']) if r['scope']=='STORE' else ('DISTRICT',geography(r['province'],province=True),geography(r['district']))
            if any(a<=d and c<=b for c,d in route_intervals[key]):raise ValueError
            route_intervals[key].append((a,b))
        proofs=catalog['evidence_hashes']
        if not isinstance(proofs,list) or not 1<=len(proofs)<=20:raise ValueError
        names=set()
        for proof in proofs:
            if (not isinstance(proof,dict) or set(proof)!={'file_id','sha256'} or
                    not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',proof['file_id']) or proof['file_id'] in names or
                    not re.fullmatch('[a-f0-9]{64}',proof['sha256'])):raise ValueError
            names.add(proof['file_id'])
        digest=catalog_digest(catalog)
        if expected_digest is not None and digest!=expected_digest:raise ValueError
        return digest
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        raise ValueError('Catalog bốc xếp sai schema/source/version/interval hoặc có quy tắc chồng lặp; chưa thay nguồn.') from None


def catalog_from_owner_confirmations(costs,models,store_match,source_pin,*,version,period,evidence_hashes):
    """RAM-only adapter for owner-reviewed business JSON. No file reads/writes."""
    end=calendar.monthrange(int(period[:4]),int(period[5:]))[1]
    last=period+f'-{end:02d}'
    if (costs.get('schema')!='supra-owner-cost-confirmations-v1' or not costs.get('deterministic_calculation_ready') or
            models.get('status')!='OWNER_ROUTING_AND_OCTOBER_EFFECTIVE_DATES_CONFIRMED'):
        raise ValueError('Chưa có xác nhận điều khoản/mô hình đủ phạm vi.')
    catalog=dict(schema='supra-dc-handling-catalog-v1',version=version,source_pin=source_pin,period=period,
        provenance='USER_CONFIRMED_IN_CODEX',confirmed_date=costs['confirmed_date'],rates=[],routing=[],evidence_hashes=evidence_hashes)
    for i,r in enumerate(costs['rates']):
        if (r['cost_component']!='DC_HANDLING' or r['applicable_month']!=period or r['currency']!='VND' or
                r['vehicle_scope']!='TRANSFER_VEHICLES_ONLY' or r['billing_date_basis']['column']!='E' or
                r['billable_weight_source']['column']!='L' or r['billable_weight_source']['unit']!='kg' or
                r['billable_weight_source']['grain']!='GHN_TOTAL_ACROSS_DO' or
                r['billable_weight_source']['aggregation']!='COUNT_ONCE_PER_ELIGIBLE_GHN' or
                not r.get('effective_from_inclusive') or r.get('effective_to_inclusive',True) is not True):
            raise ValueError('Điều khoản bốc xếp chưa khớp grain/đơn vị/phạm vi.')
        catalog['rates'].append(dict(rate_id='R'+str(i+1),**{'from':r['effective_from'],'to':r.get('effective_to_confirmed') or last},
            vnd_per_kg=str(r['rate_vnd_per_kg']),currency='VND',evidence_ref='owner_cost_confirmations.json#/rates/'+str(i)))
    for i,r in enumerate(models['rules']):
        if r['matching_scope'] not in ('STORE_EXCEPTION','DISTRICT'):
            raise ValueError('Mô hình chưa rõ cấp huyện hoặc ngoại lệ cửa hàng.')
        scope='STORE' if r['matching_scope']=='STORE_EXCEPTION' else 'DISTRICT'
        if (r['routing_confirmation_provenance']!='USER_CONFIRMED_IN_CODEX' or r['routing_mode_confirmed'] not in ('TRANSFER','DIRECT') or
                r['dc_handling_applicability_confirmed']!=(r['routing_mode_confirmed']=='TRANSFER') or
                r.get('effective_from_inclusive') is not True or r.get('effective_to_inclusive') is not True):
            raise ValueError('Mô hình chưa được owner chốt routing/hiệu lực.')
        district=r['area'];store_id=''
        if scope=='STORE':
            if (not isinstance(store_match,dict) or store_match['source_store_column']!='H' or store_match['source_name_column']!='I' or
                    geography(store_match['user_rule_area'])!=geography(r['area']) or
                    store_match['source_tab']!=source_pin['tab'] or str(store_match['gid'])!=str(source_pin['gid'])):
                raise ValueError('Đối chiếu ngoại lệ cửa hàng chưa khớp nguồn/nhãn chính xác.')
            district=store_match['observed_source_district'];store_id=store_match['matched_store_id']
        catalog['routing'].append(dict(rule_id='M'+str(i+1),scope=scope,province=r['province'],district=district,
            store_id=store_id,routing=r['routing_mode_confirmed'],**{'from':r['effective_from_confirmed'],'to':r['effective_to_confirmed']},
            evidence_ref='owner_delivery_models.json#/rules/'+str(i)))
    validate_catalog(catalog,source_pin)
    return catalog


def _route(row,catalog):
    day=row['dispatch_date']
    if day is None:return None
    day=str(day);province=geography(row['source_province'],province=True);district=geography(row['source_district'])
    active=[r for r in catalog['routing'] if r['from']<=day<=r['to']]
    store=[r for r in active if r['scope']=='STORE' and r['store_id']==row['source_store_id']]
    if store:
        matches=[r for r in store if geography(r['province'],province=True)==province and geography(r['district'])==district]
        return matches[0]['routing'] if len(matches)==len(store)==1 else None
    if not row['source_store_id'] and any(r['scope']=='STORE' and geography(r['province'],province=True)==province and geography(r['district'])==district for r in active):
        return None
    matches=[r for r in active if r['scope']=='DISTRICT' and geography(r['province'],province=True)==province and geography(r['district'])==district]
    return matches[0]['routing'] if len(matches)==1 else None


def handling_summary(data,scoped,period,cutoff):
    policy=getattr(data,'handling_policy',None);catalog=getattr(data,'handling_catalog',None)
    required=('source_ghn_weight','source_province','source_district','source_store_id')
    available=policy==HANDLING_POLICY and bool(catalog) and data.weight_rules==WEIGHT_RULE and all(k in data.rows for k in required)
    result=dict(available=available,complete=False,component_vnd=None,eligible_vnd=None,eligible_kg=None,
        eligible_transfer_ghn=0,direct_excluded_ghn=0,unresolved_ghn=0,missing_id_rows=0,
        counters={k:0 for k in ('partial_scope','unknown_routing','mixed_routing','unknown_rate','mixed_rate','missing_weight','invalid_weight','conflicting_weight')},
        rate_buckets=[],issue_sources=[],sources_by_metric={},group_evidence=[])
    if not available:
        result['reason']='MISSING_APPROVED_HANDLING_CONFIG_OR_FIELDS';return result
    if not isinstance(getattr(data,'handling_catalog_sha256',None),str):
        raise ValueError('Component bốc xếp thiếu tham chiếu hash catalog.')
    source=catalog['source_pin']
    validate_catalog(catalog,source,policy,getattr(data,'handling_catalog_sha256',None))
    result.update(catalog_version=catalog['version'],catalog_sha256=catalog_digest(catalog),policy_version=policy['version'],
        verification='OWNER_TERMS_CALCULATION_NOT_SETTLED_COST')
    whole=defaultdict(list)
    for r in data.rows.to_dict('records'):
        if r['order_id']:whole[r['order_id']].append(r)
    result['missing_id_rows']=int(scoped.order_id.eq('').sum())
    result['issue_sources']+=scoped.loc[scoped.order_id.eq(''),'_source'].tolist()
    buckets={r['rate_id']:dict(rate_vnd_per_kg=r['vnd_per_kg'],effective_from=r['from'],effective_to=r['to'],
        eligible_ghn=0,eligible_kg=Decimal(0),eligible_vnd=Decimal(0),sources=[]) for r in catalog['rates']}
    with localcontext() as ctx:
        ctx.prec=140
        for ghn in sorted(set(scoped.order_id)-{''}):
            records=whole[ghn];refs=[r['_source'] for r in records];issues=[]
            if any(r['dispatch_date'] is None or r['dispatch_date'].strftime('%Y-%m')!=period or r['dispatch_date']>cutoff.date() for r in records):
                issues.append('partial_scope')
            modes={_route(r,catalog) for r in records}
            if None in modes:issues.append('unknown_routing')
            if {'TRANSFER','DIRECT'}.issubset(modes):issues.append('mixed_routing')
            if not issues and modes=={'DIRECT'}:
                result['direct_excluded_ghn']+=1
                result['sources_by_metric'].setdefault('direct_excluded_ghn',[]).extend(refs)
                continue
            rate_ids=set()
            if 'TRANSFER' in modes:
                for r in records:
                    matching=[rate['rate_id'] for rate in catalog['rates'] if r['dispatch_date'] is not None and rate['from']<=str(r['dispatch_date'])<=rate['to']]
                    if len(matching)!=1:issues.append('unknown_rate')
                    else:rate_ids.add(matching[0])
                if len(rate_ids)>1:issues.append('mixed_rate')
                weights=[parse_kg(r['source_ghn_weight']) for r in records]
                totals={v for v,s in weights if s=='VALID'}
                if all(s=='MISSING' for v,s in weights):issues.append('missing_weight')
                if any(s=='INVALID' for v,s in weights):issues.append('invalid_weight')
                if len(totals)>1:issues.append('conflicting_weight')
            else:totals=set()
            if issues:
                result['unresolved_ghn']+=1;result['issue_sources']+=refs
                for key in set(issues):result['counters'][key]+=1
                result['group_evidence'].append(dict(order_id=ghn,issues=sorted(set(issues)),sources=refs))
                continue
            if modes!={'TRANSFER'} or len(totals)!=1 or len(rate_ids)!=1:
                raise ValueError('Bốc xếp chưa đủ grain; không chọn dòng/giá mặc định.')
            key=next(iter(rate_ids));kg=next(iter(totals));b=buckets[key]
            b['eligible_ghn']+=1;b['eligible_kg']+=kg;b['eligible_vnd']+=kg*Decimal(b['rate_vnd_per_kg']);b['sources']+=refs
            result['eligible_transfer_ghn']+=1
        accepted=[b for b in buckets.values() if b['eligible_ghn']]
        has_known=bool(accepted) or (result['direct_excluded_ghn']>0 and not result['unresolved_ghn'] and not result['missing_id_rows'])
        result['eligible_vnd']=str(sum((b['eligible_vnd'] for b in accepted),Decimal(0))) if has_known else None
        result['eligible_kg']=str(sum((b['eligible_kg'] for b in accepted),Decimal(0))) if has_known else None
    result['complete']=bool(len(scoped)) and not result['unresolved_ghn'] and not result['missing_id_rows']
    result['component_vnd']=result['eligible_vnd'] if result['complete'] else None
    for b in buckets.values():
        result['rate_buckets'].append({**b,'eligible_kg':str(b['eligible_kg']) if b['eligible_ghn'] else None,
            'eligible_vnd':str(b['eligible_vnd']) if b['eligible_ghn'] else None})
    result['reason']='OWNER_TERMS_COMPLETE' if result['complete'] else 'PARTIAL_COMPONENT_NOT_WHOLE_SCOPE'
    return result
