from datetime import date
from app.config import RULE_VERSION
def evaluate(rows,tolerance):
    result=[]
    for row in rows:
        if row['state']=='缺测':code='GAP';state='待补测'
        elif abs(row['residual'])>tolerance:code='LIMIT';state='待复核'
        else:code='PASS';state='通过'
        result.append({**row,'code':code,'state':state})
    return result
# 跨日偏差预警：同一测站连续完整日的收支差同向累计，越过警戒线即预警。
# 缺测日与日期断裂处断开不连算，避免缺口两侧水量混算误报；只给偏差事实，不推断原因。
def cumulative(rows,threshold):
    stations={}
    for row in rows:stations.setdefault(row['station'],[]).append(row)
    alerts=[]
    for station in sorted(stations):
        segment=None;previous=None
        for row in sorted(stations[station],key=lambda r:r['date']):
            if row['state']!='完整':
                segment=_settle(segment,alerts,station,threshold);previous=None;continue
            day=date.fromisoformat(row['date'])
            if previous is not None and (day-previous).days!=1:
                segment=_settle(segment,alerts,station,threshold)
            previous=day
            residual=row['residual'];sign=1 if residual>0 else (-1 if residual<0 else 0)
            if segment is None:
                segment={'sign':sign,'total':residual,'accounts':[_account(row)]}
            elif sign==0 or segment['sign']==0 or sign==segment['sign']:
                segment['total']+=residual;segment['accounts'].append(_account(row))
                if segment['sign']==0:segment['sign']=sign
            else:
                segment=_settle(segment,alerts,station,threshold)
                segment={'sign':sign,'total':residual,'accounts':[_account(row)]}
        _settle(segment,alerts,station,threshold)
    return alerts
def _settle(segment,alerts,station,threshold):
    if segment and segment['sign'] and abs(segment['total'])>threshold:
        days=len(segment['accounts']);total=segment['total']
        alerts.append({'station':station,'direction':'正向' if segment['sign']>0 else '负向',
            'start_date':segment['accounts'][0]['date'],'end_date':segment['accounts'][-1]['date'],
            'days':days,'cumulative_m3':total,'threshold_m3':threshold,'rules':RULE_VERSION,
            'basis':f"连续{days}个完整日收支差同向累计{total}m³，越过警戒线{threshold}m³；缺测日与日期断裂不连算，单日裁决见异常复核表",
            'accounts':segment['accounts']})
    return None
def _account(row):
    return {'date':row['date'],'residual':row['residual'],'coverage_hours':row['coverage_hours'],'state':row['state']}
