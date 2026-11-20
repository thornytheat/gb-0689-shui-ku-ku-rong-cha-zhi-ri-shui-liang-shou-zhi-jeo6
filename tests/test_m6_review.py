import pytest
from decimal import Decimal as D
from datetime import datetime,timedelta,timezone
from app.errors import DomainError
from app.pipeline import run
from app.normalize import instant
from app import store
from app.modules.m6_review.rules import evaluate,cumulative

ZONE=timezone(timedelta(hours=8))
def day(date,residual,state='完整'):
    return {'station':'甲','date':date,'residual':D(residual),'coverage_hours':D(24),'state':state}
def drift(days,drop=(),extra=None):
    lines=['id,station,time,level,inflow,outflow,flow_unit,version'];n=0
    for i in range(days*24+1):
        if i in drop: continue
        n+=1;t=datetime(2026,11,10,tzinfo=ZONE)+timedelta(hours=i)
        lines.append(f"{n:05d},青岚库,{t.isoformat()},{101+i*0.001:.3f},1.5,1.5,m3/s,1")
    if extra: lines.append(extra)
    return "\n".join(lines)
def alert_rows():
    with store.connection() as db:
        return [dict(r) for r in db.execute("SELECT * FROM alerts ORDER BY alert_key,version").fetchall()]

def test_incomplete_priority():
    assert evaluate([{'state':'缺测','residual':D(999)}],D(1))[0]['code']=='GAP'

def test_threshold_included():
    assert evaluate([{'state':'完整','residual':D(500)}],D(500))[0]['state']=='通过'

def test_negative_residual():
    assert evaluate([{'state':'完整','residual':D(-501)}],D(500))[0]['code']=='LIMIT'

def test_zero_residual():
    assert evaluate([{'state':'完整','residual':D(0)}],D(0))[0]['state']=='通过'

def test_real_review_present(raw):
    assert len(run(raw,stop=6,persist=False)['review_rows'])>=2

def test_real_missing_blocks(raw):
    assert any(x['state']=='待补测' for x in run(raw,stop=6,persist=False)['review_rows'])

def test_real_reasons(raw):
    assert set(x['code'] for x in run(raw,stop=6,persist=False)['review_rows'])<=set(['PASS','GAP','LIMIT'])

def test_cumulative_cross_day():
    rows=[day('2026-11-10',400),day('2026-11-11',400),day('2026-11-12',400)]
    alerts=cumulative(rows,D(1000))
    assert len(alerts)==1 and alerts[0]['days']==3 and alerts[0]['cumulative_m3']==D(1200)
    assert alerts[0]['direction']=='正向' and alerts[0]['start_date']=='2026-11-10' and alerts[0]['end_date']=='2026-11-12'
    assert [a['date'] for a in alerts[0]['accounts']]==['2026-11-10','2026-11-11','2026-11-12']

def test_cumulative_gap_breaks():
    rows=[day('2026-11-10',400),day('2026-11-11',400),day('2026-11-12',0,'缺测'),day('2026-11-13',400),day('2026-11-14',400)]
    assert cumulative(rows,D(1000))==[]

def test_cumulative_date_break():
    rows=[day('2026-11-10',400),day('2026-11-11',400),day('2026-11-13',400),day('2026-11-14',400)]
    assert cumulative(rows,D(1000))==[]

def test_cumulative_sign_flip():
    rows=[day('2026-11-10',400),day('2026-11-11',400),day('2026-11-12',-400),day('2026-11-13',400),day('2026-11-14',400)]
    assert cumulative(rows,D(1000))==[]

def test_cumulative_threshold_included():
    rows=[day('2026-11-10',500),day('2026-11-11',500)]
    assert cumulative(rows,D(1000))==[]

def test_cumulative_zero_joins():
    rows=[day('2026-11-10',0),day('2026-11-11',400),day('2026-11-12',400),day('2026-11-13',400)]
    alerts=cumulative(rows,D(1000))
    assert len(alerts)==1 and alerts[0]['days']==4 and alerts[0]['cumulative_m3']==D(1200)

def test_cumulative_negative():
    rows=[day('2026-11-10',-400),day('2026-11-11',-400),day('2026-11-12',-400)]
    alerts=cumulative(rows,D(1000))
    assert len(alerts)==1 and alerts[0]['direction']=='负向' and alerts[0]['cumulative_m3']==D(-1200)

def test_drift_alert_below_daily_limit():
    result=run(drift(4),persist=False)
    assert all(r['state']=='通过' for r in result['review_rows'])
    assert len(result['alerts'])==1
    alert=result['alerts'][0]
    assert alert['station']=='青岚库' and alert['days']==4 and D(alert['cumulative_m3'])==D(1440)
    assert alert['start_date']=='2026-11-10' and alert['end_date']=='2026-11-13'
    assert '1440' in alert['basis'] and '1000' in alert['basis']

def test_drift_gap_no_alert():
    result=run(drift(5,drop={58,59,60}),persist=False)
    assert result['alerts']==[]
    assert any(r['state']=='待补测' for r in result['review_rows'])

def test_rerun_no_new_alerts(isolate):
    run(drift(4));first=alert_rows();run(drift(4));second=alert_rows()
    assert len(first)==1 and len(second)==1
    assert first[0]['status']=='有效' and second[0]['content_hash']==first[0]['content_hash']

def test_late_measurement_updates_alert(isolate):
    run(drift(4,drop={34}))
    late=f"B0001,青岚库,{(datetime(2026,11,10,tzinfo=ZONE)+timedelta(hours=34)).isoformat()},101.034,1.55,1.5,m3/s,1"
    run(drift(4,drop={34},extra=late))
    rows=alert_rows()
    assert len(rows)==2 and rows[0]['alert_key']==rows[1]['alert_key']
    assert rows[0]['version']==1 and rows[0]['status']=='已更新' and D(rows[0]['cumulative_m3'])==D(1440)
    assert rows[1]['version']==2 and rows[1]['status']=='有效' and D(rows[1]['cumulative_m3'])==D(1260)

def test_late_measurement_supersedes_alert(isolate):
    run(drift(5,drop={82,83,84}))
    assert [r['status'] for r in alert_rows()]==['有效']
    run(drift(5))
    rows=alert_rows()
    assert len(rows)==2
    old=[r for r in rows if r['end_date']=='2026-11-12'];new=[r for r in rows if r['end_date']=='2026-11-14']
    assert len(old)==1 and old[0]['status']=='已失效'
    assert len(new)==1 and new[0]['status']=='有效' and new[0]['days']==5

def test_alerts_api(client):
    reply=client.post("/api/runs",json={"text":drift(4)})
    run_id=reply.json["manifest"]["run_id"]
    assert len(reply.json["alerts"])==1
    trail=client.get("/api/alerts",query_string={"run":run_id})
    assert trail.status_code==200 and len(trail.json["alerts"])==1
    assert any(v["status"]=="有效" for v in trail.json["trail"])
    assert client.get("/api/alerts").status_code==422

def test_alert_page(client):
    run_id=client.post("/api/runs",json={"text":drift(4)}).json["manifest"]["run_id"]
    page=client.get("/m/6",query_string={"run":run_id})
    assert page.status_code==200 and "跨日偏差预警" in page.get_data(as_text=True)

