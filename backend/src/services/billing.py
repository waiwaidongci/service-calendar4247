"""账单与核对会话的数据库编排。纯判定逻辑在recon_core.py。
并发约定：占用与差异在同一事务原子发布；会话行锁串行化同会话操作；
账单号唯一约束保证跨会话不重复占用；代数比对拒绝迟到计算。"""
import time
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from src.models.run import Run
from src.models.billing import Bill,BillConflict,BillBatch,ReconSession,Claim,Diff
from src.services import recon_core
from src.services.audit import log
from src.services.calculation import business_minutes
from src.utils.serialize import row
def import_bills(db,user,name,text):
 """整批导入为一个事务：全部生效或全部回滚；重复回传逐行幂等。"""
 rows=recon_core.parse_bills_csv(text)
 nos=sorted({r['bill_no'] for r in rows})
 stored={}
 for b in db.query(Bill).filter(Bill.bill_no.in_(nos)).all(): stored[(b.bill_no,b.revision)]={'request_ref':b.request_ref,'minutes':b.minutes}
 to_insert,conflicts,counts=recon_core.plan_import(rows,stored)
 batch=BillBatch(name=name,received=counts['received'],imported=counts['imported'],duplicate=counts['duplicate'],stale=counts['stale'],conflict=counts['conflict'],created=time.time())
 db.add(batch);db.flush();now=time.time()
 for r in to_insert: db.add(Bill(bill_no=r['bill_no'],revision=r['revision'],request_ref=r['request_ref'],minutes=r['minutes'],batch_id=batch.id,created=now))
 for r in conflicts: db.add(BillConflict(batch_id=batch.id,bill_no=r['bill_no'],revision=r['revision'],request_ref=r['request_ref'],minutes=r['minutes'],reason='同版异值与已存记录不一致，已隔离',created=now))
 log(db,user.email,'bill.import',f"批次{name}：收{counts['received']} 入{counts['imported']} 重{counts['duplicate']} 旧{counts['stale']} 隔离{counts['conflict']}")
 try: db.commit()
 except IntegrityError:
  db.rollback();raise HTTPException(409,'并发导入相同账单号，请重试')
 return batch
def _stored_current(db):
 return recon_core.current_bills([{'bill_no':b.bill_no,'revision':b.revision,'request_ref':b.request_ref,'minutes':b.minutes} for b in db.query(Bill).all()])
def _checked_run(db,run_id):
 run=db.get(Run,run_id)
 if not run: raise HTTPException(404,'报告不存在')
 if run.status!='completed' or not run.approved: raise HTTPException(409,'仅可对已复核且已完成的报告核对')
 return run
def _publish(db,user,session_id,run,expect_gen,action):
 """整批释放旧占用→认领新占用→改写差异→推进代数，单事务提交；
 任一账单被其他会话占用则整体回滚，保留旧结果。"""
 try: expected=recon_core.expected_minutes(run.snapshot or {},business_minutes)
 except ValueError as exc: raise HTTPException(409,str(exc))
 bills=_stored_current(db)
 diffs=recon_core.build_diffs(expected,bills)
 wanted=sorted(no for no,b in bills.items() if b['request_ref'] in expected)
 locked=db.query(ReconSession).filter(ReconSession.id==session_id).with_for_update().first()
 if not locked: raise HTTPException(404,'核对会话不存在')
 if locked.generation!=expect_gen: raise HTTPException(409,'会话已被其他操作更新，请刷新后重试')
 gen=expect_gen+1;now=time.time()
 try:
  db.query(Claim).filter(Claim.session_id==session_id).delete(synchronize_session=False);db.flush()
  for no in wanted: db.add(Claim(session_id=session_id,bill_no=no,request_ref=bills[no]['request_ref'],generation=gen,created=now))
  db.query(Diff).filter(Diff.session_id==session_id).delete(synchronize_session=False);db.flush()
  for d in diffs: db.add(Diff(session_id=session_id,generation=gen,created=now,**d))
  locked.run_id=run.id;locked.generation=gen;locked.updated=now
  log(db,user.email,action,f'会话{session_id}→报告{run.id} 第{gen}代 占用{len(wanted)}单 差异{len(diffs)}笔')
  db.commit()
 except IntegrityError:
  db.rollback()
  held=db.query(Claim).filter(Claim.bill_no.in_(wanted),Claim.session_id!=session_id).all() if wanted else []
  names='、'.join(sorted({h.bill_no for h in held})[:5]) or '未知账单'
  log(db,user.email,'recon.conflict',f'会话{session_id}执行{action}时占用冲突，保留旧结果：{names}')
  db.commit()
  raise HTTPException(409,f'部分账单已被其他核对会话占用（{names}），已保留旧结果')
 return locked
def create_session(db,user,name,run_id):
 run=_checked_run(db,run_id)
 if db.query(ReconSession).filter(ReconSession.name==name).first(): raise HTTPException(409,'核对会话名称已存在，请换名或先释放旧会话')
 session=ReconSession(name=name,run_id=run.id,generation=0,created=time.time(),updated=time.time())
 db.add(session)
 try: db.flush()
 except IntegrityError:
  db.rollback();raise HTTPException(409,'核对会话名称已存在，请换名或先释放旧会话')
 return _publish(db,user,session.id,run,0,'recon.create')
def reselect(db,user,session_id,run_id):
 session=db.get(ReconSession,session_id)
 if not session: raise HTTPException(404,'核对会话不存在')
 run=_checked_run(db,run_id)
 return _publish(db,user,session.id,run,session.generation,'recon.report')
def reconcile(db,user,session_id):
 session=db.get(ReconSession,session_id)
 if not session: raise HTTPException(404,'核对会话不存在')
 run=_checked_run(db,session.run_id)
 return _publish(db,user,session.id,run,session.generation,'recon.reconcile')
def delete_session(db,user,session_id):
 session=db.get(ReconSession,session_id)
 if not session: raise HTTPException(404,'核对会话不存在')
 occupied=db.query(Claim).filter(Claim.session_id==session_id).count()
 db.query(Claim).filter(Claim.session_id==session_id).delete(synchronize_session=False)
 db.query(Diff).filter(Diff.session_id==session_id).delete(synchronize_session=False)
 db.delete(session)
 log(db,user.email,'recon.release',f'会话{session_id}整批释放{occupied}单占用并删除')
 db.commit()
 return {'released':session_id,'occupied':occupied}
def list_sessions(db):
 runs={r.id:r for r in db.query(Run).all()};out=[]
 for s in db.query(ReconSession).order_by(ReconSession.id.desc()).all():
  counts={}
  for category,n in db.query(Diff.category,func.count()).filter(Diff.session_id==s.id,Diff.generation==s.generation).group_by(Diff.category).all(): counts[category]=n
  run=runs.get(s.run_id)
  out.append({**row(s),'counts':counts,'occupied':db.query(Claim).filter(Claim.session_id==s.id).count(),'run_name':run.name if run else '报告已缺失'})
 return out
def list_bills(db):
 claims={c.bill_no:c.session_id for c in db.query(Claim).all()}
 return [{**b,'occupied_by':claims.get(no)} for no,b in sorted(_stored_current(db).items())]
def detail(db,session_id):
 session=db.get(ReconSession,session_id)
 if not session: raise HTTPException(404,'核对会话不存在')
 run=db.get(Run,session.run_id)
 diffs=[row(d) for d in db.query(Diff).filter(Diff.session_id==session_id,Diff.generation==session.generation).all()]
 order={'分钟差':0,'漏单':1,'多单':2,'吻合':3}
 diffs.sort(key=lambda d:(order.get(d['category'],9),d['request_ref']))
 claims=[{'bill_no':c.bill_no,'request_ref':c.request_ref} for c in db.query(Claim).filter(Claim.session_id==session_id).order_by(Claim.bill_no).all()]
 return {'session':row(session),'run':{'id':run.id,'name':run.name,'checksum':run.checksum,'approved':run.approved} if run else None,'diffs':diffs,'claims':claims,'trace':recon_core.build_trace(diffs)}
