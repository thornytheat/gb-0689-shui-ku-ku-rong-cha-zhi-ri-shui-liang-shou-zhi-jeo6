import os,sqlite3,json,hashlib
from pathlib import Path
from datetime import datetime,timezone
from app.errors import DomainError
def connection():
    path=Path(os.environ.get("DATA_DIR","data"));path.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path/"runs.sqlite",timeout=15)
    db.row_factory=sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,review TEXT NOT NULL DEFAULT '待复核')")
    db.execute("""CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT,alert_key TEXT NOT NULL,version INTEGER NOT NULL,
        run_id TEXT NOT NULL,station TEXT NOT NULL,direction TEXT NOT NULL,start_date TEXT NOT NULL,end_date TEXT NOT NULL,
        days INTEGER NOT NULL,cumulative_m3 TEXT NOT NULL,threshold_m3 TEXT NOT NULL,basis TEXT NOT NULL,
        content_hash TEXT NOT NULL,status TEXT NOT NULL DEFAULT '有效',retired_run_id TEXT,created_at TEXT NOT NULL)""")
    return db
def save(result):
    with connection() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES(?,?)",(result["manifest"]["run_id"],json.dumps(result,ensure_ascii=False)))
        _sync_alerts(db,result)
    return fetch(result["manifest"]["run_id"])
def fetch(run_id):
    with connection() as db: row=db.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
    if row is None: raise DomainError("NOT_FOUND","批次不存在",404)
    result=json.loads(row["payload"]);result["version"]=row["version"];result["review"]=row["review"]
    return result
def history():
    with connection() as db: rows=db.execute("SELECT id,version,review FROM runs ORDER BY rowid DESC LIMIT 100").fetchall()
    return [dict(row) for row in rows]
def review(run_id,version,decision):
    if decision not in ("通过","退回"): raise DomainError("REVIEW","请选择通过或退回")
    if not isinstance(version,int): raise DomainError("STALE","版本必须为整数")
    with connection() as db:
        cur=db.execute("UPDATE runs SET review=?,version=version+1 WHERE id=? AND version=?",(decision,run_id,version))
        if cur.rowcount!=1: raise DomainError("STALE","复核版本已更新",409)
    return fetch(run_id)
# 预警身份=测站+起止日+方向；内容含累计值、涉及日账与规则版本。
# 同身份同内容不新增（同批重跑幂等）；同身份内容变化升版本，旧版标记已更新；
# 本次测站日期范围内不再成立的预警标记已失效。所有版本保留可追查。
def _alert_key(alert):
    raw='|'.join([alert['station'],alert['start_date'],alert['end_date'],alert['direction']])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
def _content_hash(alert):
    payload=json.dumps({k:alert[k] for k in ('station','direction','start_date','end_date','days','cumulative_m3','threshold_m3','accounts','rules')},sort_keys=True,ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()
def _sync_alerts(db,result):
    run_id=result["manifest"]["run_id"];now=datetime.now(timezone.utc).isoformat();seen=set()
    for alert in result.get("alerts",[]):
        key=_alert_key(alert);seen.add(key);content=_content_hash(alert)
        latest=db.execute("SELECT version,content_hash FROM alerts WHERE alert_key=? ORDER BY version DESC LIMIT 1",(key,)).fetchone()
        if latest and latest["content_hash"]==content: continue
        if latest: db.execute("UPDATE alerts SET status='已更新',retired_run_id=? WHERE alert_key=? AND status='有效'",(run_id,key))
        db.execute("INSERT INTO alerts(alert_key,version,run_id,station,direction,start_date,end_date,days,cumulative_m3,threshold_m3,basis,content_hash,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key,(latest["version"]+1) if latest else 1,run_id,alert["station"],alert["direction"],alert["start_date"],alert["end_date"],
             alert["days"],str(alert["cumulative_m3"]),str(alert["threshold_m3"]),json.dumps(alert,ensure_ascii=False),content,'有效',now))
    scope={}
    for row in result.get("budgets",[]):
        low,high=scope.get(row["station"],(row["date"],row["date"]))
        scope[row["station"]]=(min(low,row["date"]),max(high,row["date"]))
    for station,(low,high) in scope.items():
        if seen:
            marks=",".join("?"*len(seen))
            db.execute(f"UPDATE alerts SET status='已失效',retired_run_id=? WHERE status='有效' AND station=? AND start_date>=? AND end_date<=? AND alert_key NOT IN ({marks})",(run_id,station,low,high,*seen))
        else:
            db.execute("UPDATE alerts SET status='已失效',retired_run_id=? WHERE status='有效' AND station=? AND start_date>=? AND end_date<=?",(run_id,station,low,high))
def alert_trail(run_id):
    with connection() as db:
        keys=[row["alert_key"] for row in db.execute("SELECT DISTINCT alert_key FROM alerts WHERE run_id=? OR retired_run_id=?",(run_id,run_id)).fetchall()]
        if not keys: return []
        marks=",".join("?"*len(keys))
        rows=db.execute(f"SELECT * FROM alerts WHERE alert_key IN ({marks}) ORDER BY station,start_date,alert_key,version",keys).fetchall()
    trail=[]
    for row in rows:
        item=dict(row);item["basis"]=json.loads(item["basis"]);trail.append(item)
    return trail
