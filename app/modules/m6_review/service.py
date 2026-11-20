from .rules import evaluate,cumulative
from app.normalize import number
def execute(ctx):
    rows=evaluate(ctx['budgets'],number(ctx['params']['tolerance_m3']));ctx['review_rows']=rows
    alerts=cumulative(ctx['budgets'],number(ctx['params']['cumulative_tolerance_m3']));ctx['alerts']=alerts
    ctx['stages'].append({'title':'异常复核','rows':rows,'metrics':{'通过日':sum(r['state']=='通过' for r in rows),'待补测':sum(r['code']=='GAP' for r in rows),'超差日':sum(r['code']=='LIMIT' for r in rows),'跨日预警':len(alerts)}});return ctx
