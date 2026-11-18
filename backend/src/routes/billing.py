from fastapi import APIRouter,Depends
from src.config.database import database
from src.controllers.auth import current,editor
from src.models.billing import BillConflict,BillBatch
from src.schemas.requests import BillImport,ReconCreate,ReconReselect
from src.services import billing
from src.utils.serialize import row
router=APIRouter()
@router.post('/bills/import')
def import_bills(b:BillImport,db=Depends(database),u=Depends(editor)): return row(billing.import_bills(db,u,b.name,b.csv))
@router.get('/bills')
def bills(db=Depends(database),u=Depends(current)): return billing.list_bills(db)
@router.get('/bills/conflicts')
def conflicts(db=Depends(database),u=Depends(current)): return [row(x) for x in db.query(BillConflict).order_by(BillConflict.id.desc()).limit(200).all()]
@router.get('/bills/batches')
def batches(db=Depends(database),u=Depends(current)): return [row(x) for x in db.query(BillBatch).order_by(BillBatch.id.desc()).limit(100).all()]
@router.post('/recon/sessions')
def create(b:ReconCreate,db=Depends(database),u=Depends(editor)): return row(billing.create_session(db,u,b.name,b.run_id))
@router.get('/recon/sessions')
def sessions(db=Depends(database),u=Depends(current)): return billing.list_sessions(db)
@router.get('/recon/sessions/{id}')
def session_detail(id:int,db=Depends(database),u=Depends(current)): return billing.detail(db,id)
@router.post('/recon/sessions/{id}/report')
def reselect(id:int,b:ReconReselect,db=Depends(database),u=Depends(editor)): return row(billing.reselect(db,u,id,b.run_id))
@router.post('/recon/sessions/{id}/reconcile')
def reconcile(id:int,db=Depends(database),u=Depends(editor)): return row(billing.reconcile(db,u,id))
@router.delete('/recon/sessions/{id}')
def release(id:int,db=Depends(database),u=Depends(editor)): return billing.delete_session(db,u,id)
