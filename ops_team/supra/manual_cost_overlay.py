"""Revenue and owner allocations on top of the unchanged manual cost report."""
from collections import defaultdict
from decimal import Decimal,localcontext
import hashlib,json
from .booking_handling import geography
from .booking_quantity import quantity_summary,business_key,cell
from .booking_vehicles import RAW_HEADERS,DIRECT_MODES,day,plate
from ..telegram_cost_overlay import FILENAME,validate_overlay
from ..telegram_manual_cost_policy import FILENAME as BASE_FILENAME
from ..telegram_vehicle_forecast import FILENAME as FORECAST_FILENAME
from ..telegram_secrets import load_protected


def apply_overlay(state,data,evidence,source,facts,owner_id,report):
    path=state.root/FILENAME
    if not path.exists():return report
    try:
        raw=load_protected(path);config=json.loads(raw)
        base_raw=load_protected(state.root/BASE_FILENAME);forecast_raw=load_protected(state.root/FORECAST_FILENAME)
        base=json.loads(base_raw)
        validate_overlay(config,base,base_raw,forecast_raw,data,source,facts,owner_id,state.control('paired'))
        if hashlib.sha256(base_raw).hexdigest()!=report['policy_sha256']:raise ValueError
        report['overlay']=calculate_overlay(data,evidence,report,base,config,hashlib.sha256(raw).hexdigest())
    except (ValueError,TypeError,KeyError,AttributeError,OSError,OverflowError):
        report['overlay']={'available':False,'reason':'OVERLAY_OR_SOURCE_MISMATCH'}
    return report


def calculate_overlay(data,evidence,report,base,config,sha):
    from .manual_cost_report import _rule
    start,end=report['scope']['start'],report['scope']['end']
    def scoped(d):return d is not None and d==d and start<=str(d)<=end
    q=quantity_summary(data,data.rows.loc[data.rows.dispatch_date.map(scoped)])
    accepted=[];unpriced=[];eligible_units=0;total=Decimal(0)
    with localcontext() as ctx:
        ctx.prec=140
        if q['available']:
            for group in q['by_ghn']:
                for line in group['do_lines']:
                    province=geography(line['province'],province=True);date_text=str(line['dispatch_date'])
                    prices=[p for p in config['revenue']['prices'] if geography(p['province'],province=True)==province and p['from']<=date_text<=p['to']]
                    if len(prices)!=1:
                        unpriced.append(line['source']);continue
                    units=line['product_units'];eligible_units+=units;total+=Decimal(units)*Decimal(prices[0]['vnd_per_unit']);accepted.append(line['source'])
        subtotal=str(total) if accepted else None
        complete=bool(q['complete'] and not unpriced)
        upstream=report['upstream'].get('eligible_pool_vnd') if report['upstream'].get('available') else None
        warehouse=report['warehouse']['shared_pool_vnd']
        allocations={'upstream_vnd':str(Decimal(upstream)*Decimal(config['upstream_ratio'])) if upstream is not None else None,
                     'warehouse_vnd':str(Decimal(warehouse)*Decimal(config['warehouse_ratio'])) if warehouse is not None else None}
        components={'points':report['points']['eligible_vnd'],'ftl':report['ftl']['eligible_vnd'],
            'gxt_vehicle':report['gxt']['vehicle_supra_vnd'],'gxt_wage':report['gxt']['wage_supra_vnd'],
            'dc_incremental':report['dc_incremental_vnd'],'upstream':allocations['upstream_vnd'],'warehouse':allocations['warehouse_vnd']}
        if report.get('remote') is not None:components['remote_base']=report['remote']['eligible_vnd']
        known=[Decimal(v) for v in components.values() if v is not None]
        cost=sum(known,Decimal(0)) if known else None
        difference=Decimal(subtotal)-cost if subtotal is not None and cost is not None else None
    # Missing S cannot be attributed to a vehicle. Count separately only when
    # ALL pinned CS rows establish an unambiguous eligible GXT route on Raw day.
    rows=data.rows.to_dict('records');orders=defaultdict(list)
    for row in rows:
        if row['order_id']:orders[row['order_id']].append(row)
    selectors=set(base['selectors']['gxt']);catalog=data.vehicle_pair['vehicle_catalog'];missing_gxt=0;missing_refs=[]
    for row in data.vehicle_pair['data_supra']['rows']:
        r=dict(zip(RAW_HEADERS,row['values']));d=day(r['load_date'])
        if cell(r['type']).upper()!='DELIVER' or not d or not start<=str(d)<=end or plate(r['truck_plate_number']):continue
        order=business_key(r['order_code']);related=orders.get(order,[])
        resolved=[_rule(x,catalog,d) for x in related]
        if resolved and all(rule is not None and rule['rule_id'] in selectors for rule in resolved) and not all(rule['mode'] in DIRECT_MODES for rule in resolved):
            missing_gxt+=1;missing_refs.append(f"Raw#{data.vehicle_pair['raw_digest'][:12]} dòng {row['sheet_row']}")
    exclusion=config.get('scope_exclusions')
    applies=bool(exclusion and exclusion['window']['start']<=start<=end<=exclusion['window']['end'])
    return {'available':True,'overlay_sha256':sha,'base_policy_sha256':config['base_policy_sha256'],
        'forecast_sha256':config['forecast_sha256'],'provenance':dict(config['provenance']),
        'price_source_sha256':config['revenue'].get('price_source_sha256'),
        'upstream_ratio':config['upstream_ratio'],'warehouse_ratio':config['warehouse_ratio'],
        'allocations':allocations,'components':components,'known_cost_subtotal':str(cost) if cost is not None else None,
        'revenue_minus_known_costs':str(difference) if difference is not None else None,
        'revenue':{'eligible_vnd':subtotal,'component_vnd':subtotal if complete else None,'complete':complete,
            'eligible_units':eligible_units,'quarantined_rows':q['quarantined_rows'],'conflicting_key_rows':q['conflicting_key_rows'],
            'unpriced_sources':unpriced,'issue_sources':q['issue_sources']},
        'missing_s_gxt_rows':missing_gxt,'missing_s_gxt_sources':missing_refs,
        'scope_exclusions':dict(exclusion) if applies else None,
        'total_cost_vnd':None,'gross_profit_vnd':None}


def projected_totals(report):
    """Owner rule (27/10/2026): publish projected pre-VAT total/GP only when every in-scope part is complete.

    Returns (total, gross_profit, blockers). Values stay None while any blocker remains;
    actual settled cost is a separate, later reconciliation and is never implied here.
    """
    overlay=report.get('overlay') or {}
    if not overlay.get('available'):return None,None,['chưa có chính sách doanh thu/phân bổ']
    checks=[(overlay['revenue']['complete'],'doanh thu chưa đủ nguồn'),
        (report['points'].get('complete',not report['points']['held']) and not report['points']['held'],'phí điểm còn nhóm giữ riêng'),
        (not report['ftl']['held'],'FTL còn nhóm giữ riêng'),
        (not report['gxt']['held'] and report['gxt'].get('complete',True),'xe GXT còn nhóm giữ riêng'),
        (report['dc'].get('component_vnd') is not None and not report['dc_overlap_unknown_sources'],'bốc xếp DC chưa chốt'),
        (not report['upstream'].get('available') or report['upstream'].get('full_pool_vnd') is not None,'xe trung chuyển chưa đủ nguồn'),
        (report['warehouse'].get('shared_pool_vnd') is not None,'nhân sự kho GXT chưa có'),
        (not report['unpriced_booking_sources'] and not report['unpriced_ncc_routes'],'còn tuyến chưa có giá'),
        (not report.get('opening_unpriced_events'),'đơn khai trương chưa có km/giá'),
        (not report.get('remote') or report['remote'].get('km_extra_vnd') is not None,'phí km NCC chưa có'),
        (not overlay['missing_s_gxt_rows'],'dòng GXT thiếu biển S'),
        (overlay.get('scope_exclusions') is not None,'thuê kho, điện/nước, chờ xe, phụ phí chưa được loại hoặc tính')]
    blockers=[text for ok,text in checks if not ok]
    if blockers or overlay['known_cost_subtotal'] is None:return None,None,blockers or ['chưa có chi phí']
    with localcontext() as ctx:
        ctx.prec=140
        total=Decimal(overlay['known_cost_subtotal'])
        return str(total),str(Decimal(overlay['revenue']['component_vnd'])-total),[]
