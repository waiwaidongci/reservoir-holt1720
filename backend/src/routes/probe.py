from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from src.config.database import connection
from src.constants.enums import Role
from src.controllers.auth import allow, current
from src.domain.spec import SPEC
from src.domain.demo import probe_demo_csv
from src.models.identity import User
from src.models.probe import HighEvent, PoolState, ProbeBatch, ProbeReading, RecalcJob
from src.schemas.requests import ProbeUpload
from src.services.audit import record
from src.services.probe import import_batch, parse_probe_rows
from src.utils import views
from src.utils.wakeup import wake

router = APIRouter()


@router.get('/pools')
def pools(db: Session = Depends(connection), actor: User = Depends(current)):
    counts = dict(db.execute(select(HighEvent.pool, func.count())
                             .where(HighEvent.state == 'active').group_by(HighEvent.pool)).all())
    return [views.pool(state, counts.get(state.pool, 0))
            for state in db.scalars(select(PoolState).order_by(PoolState.pool))]


@router.get('/events')
def events(db: Session = Depends(connection), actor: User = Depends(current)):
    query = (select(HighEvent).where(HighEvent.state == 'active')
             .order_by(HighEvent.start_at.desc()).limit(200))
    return [views.event(item) for item in db.scalars(query)]


@router.get('/events/{event_id}')
def event_detail(event_id: int, db: Session = Depends(connection), actor: User = Depends(current)):
    item = db.get(HighEvent, event_id)
    if not item:
        raise HTTPException(404, '找不到该高位事件')
    readings = db.scalars(select(ProbeReading)
                          .where(ProbeReading.pool == item.pool,
                                 ProbeReading.minute >= item.start_at,
                                 ProbeReading.minute <= item.end_at)
                          .order_by(ProbeReading.minute, ProbeReading.revision.desc())).all()
    batch_ids = {reading.batch_id for reading in readings}
    names = dict(db.execute(select(ProbeBatch.id, ProbeBatch.name)
                            .where(ProbeBatch.id.in_(batch_ids or {0}))).all())
    return {**views.event(item),
            'readings': [views.reading(reading, names.get(reading.batch_id, '')) for reading in readings]}


@router.get('/probe/batches')
def probe_batches(db: Session = Depends(connection), actor: User = Depends(current)):
    return [views.probe_batch(item)
            for item in db.scalars(select(ProbeBatch).order_by(ProbeBatch.id.desc()).limit(100))]


@router.post('/probe/batches', status_code=201)
def upload_probe(body: ProbeUpload, db: Session = Depends(connection),
                 actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    rows = parse_probe_rows(body.csv_text)
    batch, created, stats, jobs = import_batch(db, body.name, body.filename, rows,
                                               actor.username, threshold=body.alert_level_m)
    if not created:
        record(db, actor.username, 'probe.batch_duplicate', batch.id, rows=batch.row_count)
    db.commit()
    if jobs:
        wake()
    return {**views.probe_batch(batch), 'created': created, 'stats': stats,
            'jobs': [views.recalc(job) for job in jobs]}


@router.get('/probe/jobs')
def probe_jobs(db: Session = Depends(connection), actor: User = Depends(current)):
    return [views.recalc(item)
            for item in db.scalars(select(RecalcJob).order_by(RecalcJob.id.desc()).limit(50))]


@router.get('/probe/template')
def probe_template(actor: User = Depends(current)):
    filename = quote(SPEC['probe']['sample_name'])
    return Response(probe_demo_csv(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{filename}"})
