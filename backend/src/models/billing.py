from sqlalchemy import Column,Integer,String,Float,JSON,UniqueConstraint
from src.config.database import Base
class Bill(Base):
 __tablename__='bills'
 id=Column(Integer,primary_key=True); bill_no=Column(String(120),nullable=False); revision=Column(Integer,nullable=False); request_ref=Column(String(120),nullable=False); minutes=Column(Float,nullable=False); batch_id=Column(Integer,index=True); created=Column(Float)
 __table_args__=(UniqueConstraint('bill_no','revision'),)
class BillConflict(Base):
 __tablename__='bill_conflicts'
 id=Column(Integer,primary_key=True); batch_id=Column(Integer,index=True); bill_no=Column(String(120)); revision=Column(Integer); request_ref=Column(String(120)); minutes=Column(Float); reason=Column(String(250)); created=Column(Float)
class BillBatch(Base):
 __tablename__='bill_batches'
 id=Column(Integer,primary_key=True); name=Column(String(120)); received=Column(Integer); imported=Column(Integer); duplicate=Column(Integer); stale=Column(Integer); conflict=Column(Integer); created=Column(Float)
class ReconSession(Base):
 __tablename__='recon_sessions'
 id=Column(Integer,primary_key=True); name=Column(String(120),unique=True); run_id=Column(Integer); generation=Column(Integer,default=0); created=Column(Float); updated=Column(Float)
class Claim(Base):
 __tablename__='bill_claims'
 id=Column(Integer,primary_key=True); session_id=Column(Integer,index=True); bill_no=Column(String(120),unique=True); request_ref=Column(String(120)); generation=Column(Integer); created=Column(Float)
class Diff(Base):
 __tablename__='recon_diffs'
 id=Column(Integer,primary_key=True); session_id=Column(Integer,index=True); generation=Column(Integer); request_ref=Column(String(120)); department=Column(String(120)); period=Column(String(120)); expected=Column(Float); billed=Column(Float); delta=Column(Float); category=Column(String(20)); bill_nos=Column(JSON); created=Column(Float)
