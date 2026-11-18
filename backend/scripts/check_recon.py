"""账单核对端到端自检：临时SQLite+桩Redis，不触碰业务库。
覆盖：逐笔重算对账、漏单/多单/分钟差、总分钟相等时的串单检出、
同版异值隔离、旧版不覆盖、重复回传幂等、跨报告占用冲突保留旧结果、
整批释放再认领、迟到计算拒绝、并发重试不重复扣占、报告原值不变。
用法：python -m scripts.check_recon"""
import os,sys,tempfile,threading
_TMP=tempfile.mkdtemp(prefix='recon-check-')
os.environ['DATABASE_URL']='sqlite:///'+os.path.join(_TMP,'check.db')
os.environ['JWT_SECRET']='recon-check-secret'
os.environ.setdefault('PROJECT_JSON','/app/project.json' if os.path.exists('/app/project.json') else os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','project.json'))
import redis
class _FakeRedis:
 def rpush(self,*a,**k): return 1
 def lpop(self,*a,**k): return None
 def ping(self): return True
redis.Redis.from_url=lambda *a,**k:_FakeRedis()
from fastapi.testclient import TestClient
from fastapi import HTTPException
from src.main import app
from src.config.database import Session
from src.config.settings import CONFIG
from src.models.identity import User
from src.models.source import Source,Policy
from src.models.run import Run
from src.models.billing import ReconSession,Claim
from src.utils.security import password_hash
from src.services.calculation import calculate,business_minutes
from src.services import billing as billing_svc
SOURCES=[('case-a',{'period':'2026-11','department':'数据建模组','opened':'2026-11-13T10:30:00+08:00','closed':'2026-11-13T16:30:00+08:00','priority':'普通'}),('case-b',{'period':'2026-11','department':'数据建模组','opened':'2026-11-13T09:00:00+08:00','closed':'2026-11-13T12:00:00+08:00','priority':'紧急'}),('case-c',{'period':'2026-11','department':'数据采样组','opened':'2026-11-17T09:00:00+08:00','closed':'2026-11-17T12:00:00+08:00','priority':'普通'}),('case-d',{'period':'2026-12','department':'数据建模组','opened':'2026-12-01T09:00:00+08:00','closed':'2026-12-01T18:00:00+08:00','priority':'普通'}),('case-e',{'period':'2026-12','department':'数据采样组','opened':'2026-12-01T14:00:00+08:00','closed':'2026-12-01T17:00:00+08:00','priority':'紧急'}),('case-f',{'period':'2026-12','department':'数据采样组','opened':'2026-12-02T10:00:00+08:00','closed':'2026-12-02T11:00:00+08:00','priority':'普通'})]
FAILED=[]
def check(name,cond,info=''):
 print(('PASS ' if cond else 'FAIL ')+name+('' if cond else ' | '+str(info)[:400]))
 if not cond: FAILED.append(name)
def csv_of(rows): return '账单号,修订号,请求编号,账单分钟\n'+'\n'.join(','.join(str(x) for x in r) for r in rows)
def main():
 with TestClient(app) as client:
  with Session() as db:
   for role in ('admin','analyst','viewer'): db.add(User(email=role+'@demo.local',role=role,password=password_hash('Demo2026!')))
   for key,data in SOURCES: db.add(Source(natural_key=key,data=data))
   db.add(Policy(version=CONFIG['policy']['version'],data=CONFIG['policy']));db.commit();policy_id=db.query(Policy).first().id
  H=lambda t:{'Authorization':'Bearer '+t}
  admin=client.post('/auth/login',json={'email':'admin@demo.local','password':'Demo2026!'}).json()['token']
  viewer=client.post('/auth/login',json={'email':'viewer@demo.local','password':'Demo2026!'}).json()['token']
  EXP={k:business_minutes(d['opened'],d['closed'],CONFIG['policy']) for k,d in SOURCES}
  check('底稿分钟手算一致',EXP=={'case-a':300,'case-b':180,'case-c':180,'case-d':480,'case-e':180,'case-f':60},EXP)
  def make_run(name,period,approve=True):
   r=client.post('/runs',json={'name':name,'policy_id':policy_id,'period':period},headers=H(admin));assert r.status_code==200,r.text
   rid=r.json()['id']
   with Session() as db:
    run=db.get(Run,rid);run.status='completed';run.result=calculate(run.snapshot['sources'],run.snapshot['policy']);db.commit()
   if approve:
    r=client.post(f'/runs/{rid}/approve',json={'note':'复核通过自检'},headers=H(admin));assert r.status_code==200,r.text
   return rid
  run_nov=make_run('十一月核算','2026-11');run_dec=make_run('十二月核算','2026-12');run_all=make_run('全年核算','全部');run_raw=make_run('未复核核算','2026-11',approve=False)
  with Session() as db: frozen=(db.get(Run,run_nov).checksum,db.get(Run,run_nov).result)
  def detail(sid): return client.get(f'/recon/sessions/{sid}',headers=H(admin)).json()
  def claims(sid): return {c['bill_no'] for c in detail(sid)['claims']}
  # 一、总分钟相等仍可能串单：+120与-120相互抵消
  r=client.post('/bills/import',json={'name':'批次一','csv':csv_of([('B-001',0,'case-a',EXP['case-a']+120),('B-002',0,'case-b',EXP['case-b']-120),('B-003',0,'case-c',EXP['case-c'])])},headers=H(admin))
  check('导入批次一',r.status_code==200 and r.json()['imported']==3,r.text)
  r=client.post('/recon/sessions',json={'name':'十一月核对','run_id':run_nov},headers=H(admin))
  check('创建会话即核对',r.status_code==200,r.text);s1=r.json()['id']
  det=detail(s1);diffs={d['request_ref']:d for d in det['diffs']}
  check('总分钟相等仍检出串单',det['trace']['cross_suspect'] is True and det['trace']['balance']==0,det['trace'])
  check('串单抵消分组定位到建模组',any(cl['department']=='数据建模组' and {m['request_ref'] for m in cl['members']}=={'case-a','case-b'} for cl in det['trace']['clusters']),det['trace']['clusters'])
  check('逐笔差值+120/-120/吻合',diffs['case-a']['delta']==120 and diffs['case-b']['delta']==-120 and diffs['case-c']['category']=='吻合',diffs)
  # 二、漏单/多单/分钟差分类；多单不占用
  r=client.post('/bills/import',json={'name':'批次二','csv':csv_of([('B-101',0,'case-d',EXP['case-d']),('B-102',0,'case-e',EXP['case-e']-60),('B-900',0,'ghost-1',60)])},headers=H(admin))
  check('导入批次二',r.status_code==200 and r.json()['imported']==3,r.text)
  r=client.post('/recon/sessions',json={'name':'十二月核对','run_id':run_dec},headers=H(admin))
  check('创建第二会话',r.status_code==200,r.text);s2=r.json()['id']
  det=detail(s2);diffs={d['request_ref']:d for d in det['diffs']}
  check('漏单/多单/分钟差/吻合分类',diffs['case-d']['category']=='吻合' and diffs['case-e']['category']=='分钟差' and diffs['case-e']['delta']==-60 and diffs['case-f']['category']=='漏单' and diffs['ghost-1']['category']=='多单',diffs)
  check('多单不参与占用',claims(s2)=={'B-101','B-102'},claims(s2))
  # 三、重复回传幂等；重试不重复扣占
  r=client.post('/bills/import',json={'name':'批次二重传','csv':csv_of([('B-101',0,'case-d',EXP['case-d']),('B-102',0,'case-e',EXP['case-e']-60),('B-900',0,'ghost-1',60)])},headers=H(admin))
  check('重复回传全部幂等忽略',r.status_code==200 and r.json()['duplicate']==3 and r.json()['imported']==0,r.text)
  gen=detail(s2)['session']['generation']
  r=client.post(f'/recon/sessions/{s2}/reconcile',headers=H(admin))
  check('重新核对成功',r.status_code==200,r.text)
  check('重试不重复扣占',claims(s2)=={'B-101','B-102'} and detail(s2)['session']['generation']==gen+1,claims(s2))
  # 四、同版异值隔离；旧版不覆盖；新修订生效
  r=client.post('/bills/import',json={'name':'批次三','csv':csv_of([('B-101',1,'case-d',EXP['case-d']+30)])},headers=H(admin))
  check('新修订入库',r.status_code==200 and r.json()['imported']==1,r.text)
  client.post(f'/recon/sessions/{s2}/reconcile',headers=H(admin))
  diffs={d['request_ref']:d for d in detail(s2)['diffs']}
  check('新修订参与重算',diffs['case-d']['delta']==30,diffs['case-d'])
  r=client.post('/bills/import',json={'name':'批次四','csv':csv_of([('B-101',1,'case-d',999)])},headers=H(admin))
  check('同版异值隔离',r.status_code==200 and r.json()['conflict']==1 and r.json()['imported']==0,r.text)
  bills={b['bill_no']:b for b in client.get('/bills',headers=H(admin)).json()}
  check('隔离不改当前值',bills['B-101']['minutes']==EXP['case-d']+30,bills['B-101'])
  check('隔离区可查',len(client.get('/bills/conflicts',headers=H(admin)).json())==1)
  r=client.post('/bills/import',json={'name':'批次五','csv':csv_of([('B-102',2,'case-e',EXP['case-e'])])},headers=H(admin))
  check('更高修订入库',r.status_code==200 and r.json()['imported']==1,r.text)
  r=client.post('/bills/import',json={'name':'批次六','csv':csv_of([('B-102',1,'case-e',EXP['case-e'])])},headers=H(admin))
  check('旧版不覆盖',r.status_code==200 and r.json()['stale']==1 and r.json()['imported']==0,r.text)
  # 五、跨报告不可重复占用；冲突保留旧结果；整批释放再认领
  r=client.post('/recon/sessions',json={'name':'全年核对','run_id':run_all},headers=H(admin))
  check('跨报告占用冲突拒绝建会话',r.status_code==409,r.text)
  check('冲突会话不落库',all(s['name']!='全年核对' for s in client.get('/recon/sessions',headers=H(admin)).json()))
  check('他会话占用不受冲击',claims(s1)=={'B-001','B-002','B-003'},claims(s1))
  before=detail(s2)['session']
  r=client.post(f'/recon/sessions/{s2}/report',json={'run_id':run_all},headers=H(admin))
  check('改选冲突409',r.status_code==409,r.text)
  after=detail(s2)['session']
  check('改选冲突保留旧结果',after['run_id']==before['run_id'] and after['generation']==before['generation'] and claims(s2)=={'B-101','B-102'},after)
  r=client.delete(f'/recon/sessions/{s1}',headers=H(admin))
  check('释放会话',r.status_code==200,r.text)
  r=client.post(f'/recon/sessions/{s2}/report',json={'run_id':run_nov},headers=H(admin))
  check('释放后改选成功',r.status_code==200,r.text)
  det=detail(s2)
  check('整批释放旧占用再认领',det['session']['run_id']==run_nov and det['session']['generation']==before['generation']+1 and claims(s2)=={'B-001','B-002','B-003'},det['session'])
  # 六、迟到计算不改新结果（代数比对）
  with Session() as db:
   s=db.get(ReconSession,s2);run=db.get(Run,run_nov);u=db.query(User).filter_by(email='admin@demo.local').first()
   try:
    billing_svc._publish(db,u,s2,run,s.generation+1,'recon.test');late=False
   except HTTPException as e: late=e.status_code==409
  check('迟到计算拒绝覆盖',late)
  # 七、并发核对：占用唯一、最终一致
  results=[]
  def spin():
   with Session() as db:
    try:
     u=db.query(User).filter_by(email='admin@demo.local').first();billing_svc.reconcile(db,u,s2);results.append('ok')
    except HTTPException as e: results.append(e.status_code)
    except Exception as e: results.append(type(e).__name__)
  gen0=detail(s2)['session']['generation']
  ts=[threading.Thread(target=spin) for _ in range(6)]
  for t in ts: t.start()
  for t in ts: t.join()
  check('并发核对无异常外结果',all(r in ('ok',409,'OperationalError') for r in results),results)
  check('并发后占用不重复不丢失',claims(s2)=={'B-001','B-002','B-003'} and detail(s2)['session']['generation']>=gen0+1,(results,claims(s2)))
  # 八、冻结内容重算：改底稿不影响已复核报告对账
  with Session() as db: sid_a=db.query(Source).filter_by(natural_key='case-a').first().id
  r=client.put(f'/sources/{sid_a}',json={'data':{'period':'2026-11','department':'数据建模组','opened':'2026-11-13T10:30:00+08:00','closed':'2026-11-13T18:30:00+08:00','priority':'普通'}},headers=H(admin))
  check('底稿可改',r.status_code==200,r.text)
  client.post(f'/recon/sessions/{s2}/reconcile',headers=H(admin))
  diffs={d['request_ref']:d for d in detail(s2)['diffs']}
  check('冻结快照重算不随底稿改动',diffs['case-a']['expected']==EXP['case-a'],diffs['case-a'])
  # 九、权限与校验；原报告与交付保持原值
  r=client.post('/bills/import',json={'name':'越权批次','csv':csv_of([('B-200',0,'case-d',1)])},headers=H(viewer))
  check('只读角色不可导入',r.status_code==403,r.text)
  r=client.post('/bills/import',json={'name':'坏批次','csv':'账单号,修订号,请求编号,账单分钟\nB-201,0,case-d,abc'},headers=H(admin))
  check('坏行整批拒绝并报行号',r.status_code==422 and '第2行' in r.json()['detail'],r.text)
  r=client.post('/recon/sessions',json={'name':'未复核核对','run_id':run_raw},headers=H(admin))
  check('未复核报告不可核对',r.status_code==409,r.text)
  with Session() as db: now=(db.get(Run,run_nov).checksum,db.get(Run,run_nov).result)
  check('原报告保持原值',now==frozen)
  r=client.delete(f'/recon/sessions/{s2}',headers=H(admin))
  check('释放后账单全部空闲',r.status_code==200 and all(b['occupied_by'] is None for b in client.get('/bills',headers=H(admin)).json()))
 if FAILED: print('\n失败 '+str(len(FAILED))+' 项：'+'、'.join(FAILED));sys.exit(1)
 print('\n全部自检通过')
if __name__=='__main__': main()
