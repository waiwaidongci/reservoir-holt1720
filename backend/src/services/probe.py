"""分钟读数导入与高位事件增量重算。

导入与重算分离：导入在同一事务内写入读数、推进池 epoch 并排起重算任务；
后台重算只处理任务记录的受影响区段，提交时校验 epoch——读数在重算期间
再次变化（epoch 已推进）时，较早的重算结果直接作废，不会盖掉新结果。
"""

import csv
import hashlib
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from src.config.database import Session
from src.constants.enums import Task
from src.domain.highwater import affected_windows, diff_events, events_from_readings
from src.domain.spec import SPEC
from src.models.probe import HighEvent, PoolState, ProbeBatch, ProbeReading, RecalcJob
from src.services.audit import record
from src.utils.time import now

log = logging.getLogger(__name__)

BEIJING = timezone(timedelta(hours=8))
MAX_ROWS = 5000
PROBE_FIELDS = ('reservoir_id', 'reading_at', 'level_m', 'revision')


def signature(rows):
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                     default=lambda value: value.isoformat())
    return hashlib.sha256(raw.encode()).hexdigest()


def parse_minute(raw):
    text = (raw or '').strip().replace(' ', 'T')
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError('读数时间需为 ISO 格式，如 2026-11-14 00:07') from None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=BEIJING)
    stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
    if stamp.second or stamp.microsecond:
        raise ValueError('读数时间需对齐到整分钟')
    return stamp


def _probe_row(raw):
    pool = (raw.get('reservoir_id') or '').strip()
    if not pool or len(pool) > 40:
        raise ValueError('水池编号不能为空且不超过 40 字')
    minute = parse_minute(raw.get('reading_at'))
    try:
        level = float(str(raw.get('level_m')).strip())
    except (TypeError, ValueError):
        raise ValueError('液位需为数字') from None
    if not 0 <= level <= 20:
        raise ValueError('液位需在 0–20 m 范围内')
    raw_revision = (raw.get('revision') or '').strip()
    try:
        revision = int(raw_revision) if raw_revision else 0
    except ValueError:
        raise ValueError('修订号需为整数') from None
    if not 0 <= revision <= 999_999:
        raise ValueError('修订号需在 0–999999 范围内')
    return {'pool': pool, 'minute': minute, 'level': level, 'revision': revision}


def parse_probe_rows(text):
    reader = csv.DictReader(io.StringIO(text.removeprefix('\ufeff'), newline=''), strict=True)
    headers = reader.fieldnames or []
    if not {'reservoir_id', 'reading_at', 'level_m'} <= set(headers) or not set(headers) <= set(PROBE_FIELDS):
        raise ValueError('CSV 表头需要 reservoir_id、reading_at、level_m，可带 revision')
    if len(headers) != len(set(headers)):
        raise ValueError('CSV 表头存在重复列')
    rows = []
    try:
        for raw in reader:
            if len(rows) >= MAX_ROWS:
                raise ValueError(f'一个回传批次最多导入 {MAX_ROWS} 行')
            try:
                if None in raw or any(value is None for value in raw.values()):
                    raise ValueError('工作表的列数与表头不一致')
                rows.append(_probe_row(raw))
            except ValueError as error:
                raise ValueError(f'CSV 第 {reader.line_num} 行 · {error}') from None
    except csv.Error:
        raise ValueError(f'CSV 第 {reader.line_num} 行附近格式不正确') from None
    if not rows:
        raise ValueError('CSV 至少需要一行分钟读数')
    # 批内同池同一分钟只保留修订号最大的读数。
    best = {}
    for row in rows:
        key = (row['pool'], row['minute'])
        if key in best:
            existing = best[key]
            if existing['revision'] == row['revision']:
                if existing['level'] != row['level']:
                    stamp = row['minute'].astimezone(BEIJING).strftime('%Y-%m-%d %H:%M')
                    raise ValueError(f'{row["pool"]} {stamp} 同一分钟出现两条修订号 {row["revision"]} 的不同读数')
                continue
            if existing['revision'] > row['revision']:
                continue
        best[key] = row
    return list(best.values())


def import_batch(db, name, filename, rows, actor, threshold=None):
    """写入一批分钟读数。返回 (批次, 是否新建, 统计, 重算任务列表)。"""
    signature_text = signature(rows)
    lookup = select(ProbeBatch).where(ProbeBatch.name == name, ProbeBatch.checksum == signature_text)
    existing = db.scalar(lookup)
    if existing:
        return existing, False, None, []
    batch = ProbeBatch(name=name, filename=filename, checksum=signature_text, row_count=len(rows), author=actor)
    try:
        with db.begin_nested():
            db.add(batch)
            db.flush()
    except IntegrityError:
        existing = db.scalar(lookup.with_for_update())
        if existing:
            return existing, False, None, []
        raise
    stats, jobs = _apply_rows(db, batch, rows, threshold)
    batch.inserted, batch.kept, batch.skipped = stats['inserted'], stats['kept'], stats['skipped']
    record(db, actor, 'probe.batch_imported', batch.id, rows=len(rows), **stats)
    return batch, True, stats, jobs


def _apply_rows(db, batch, rows, threshold):
    keys = {(row['pool'], row['minute']) for row in rows}
    stored = db.scalars(select(ProbeReading)
                        .where(tuple_(ProbeReading.pool, ProbeReading.minute).in_(keys))).all()
    by_key = {}
    for item in stored:
        by_key.setdefault((item.pool, item.minute), {})[item.revision] = item
    stats = {'inserted': 0, 'kept': 0, 'skipped': 0}
    changed = {}
    for row in rows:
        key = (row['pool'], row['minute'])
        revisions = by_key.setdefault(key, {})
        current = next((item for item in revisions.values() if not item.superseded), None)
        if row['revision'] in revisions:
            if revisions[row['revision']].level_m != row['level']:
                stamp = row['minute'].astimezone(BEIJING).strftime('%Y-%m-%d %H:%M')
                raise ValueError(f'{row["pool"]} {stamp} 修订 {row["revision"]} 与已存读数不一致，请用更大的修订号更正')
            stats['skipped'] += 1
            continue
        reading = ProbeReading(pool=row['pool'], minute=row['minute'], level_m=row['level'],
                               revision=row['revision'], batch_id=batch.id, superseded=False)
        if current is not None and row['revision'] < current.revision:
            # 乱序补传的旧修订：留档核对，不影响当前有效读数。
            reading.superseded = True
            stats['kept'] += 1
        else:
            if current is not None:
                current.superseded = True
            changed.setdefault(row['pool'], set()).add(row['minute'])
            stats['inserted'] += 1
        revisions[row['revision']] = reading
        db.add(reading)
    jobs = []
    for pool in sorted({row['pool'] for row in rows}):
        state = db.get(PoolState, pool, with_for_update=True)
        if state is None:
            try:
                with db.begin_nested():
                    state = PoolState(pool=pool, threshold_m=SPEC['probe']['default_threshold_m'], epoch=0)
                    db.add(state)
                    db.flush()
            except IntegrityError:
                state = db.get(PoolState, pool, with_for_update=True)
        full = False
        if threshold is not None and threshold != state.threshold_m:
            state.threshold_m = threshold
            full = True
            record(db, 'system', 'pool.threshold_updated', pool, threshold_m=threshold)
        minutes = changed.get(pool, set())
        if not minutes and not full:
            continue
        have = set(db.scalars(select(ProbeReading.minute)
                              .where(ProbeReading.pool == pool, ProbeReading.superseded.is_(False))))
        windows = affected_windows(have, have if full else minutes)
        if not windows:
            continue
        state.epoch += 1
        state.updated_at = now()
        job = RecalcJob(pool=pool, epoch=state.epoch, threshold_m=state.threshold_m,
                        windows=[[start.isoformat(), end.isoformat()] for start, end in windows])
        db.add(job)
        db.flush()
        jobs.append(job)
    return stats, jobs


def claim_recalc():
    stamp = now()
    with Session.begin() as db:
        query = select(RecalcJob).where(or_(RecalcJob.status == Task.QUEUED,
                                            and_(RecalcJob.status == Task.RUNNING, RecalcJob.lease_until < stamp)))
        job = db.scalar(query.order_by(RecalcJob.id).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        if job.attempt >= 3:
            job.status = Task.FAILED
            job.error = '后台进程多次中断，请重新导入触发重算'
            job.finished_at = stamp
            record(db, 'worker', 'recalc.failed', job.id, reason=job.error)
            return None
        job.status = Task.RUNNING
        job.attempt += 1
        job.lease_until = stamp + timedelta(minutes=3)
        return job.id, job.attempt


def execute_recalc(task):
    job_id, attempt = task
    failure = None
    plan = []
    try:
        with Session() as db:
            job = db.get(RecalcJob, job_id)
            if job is None:
                return
            for start_text, end_text in job.windows:
                start, end = datetime.fromisoformat(start_text), datetime.fromisoformat(end_text)
                rows = db.execute(select(ProbeReading.minute, ProbeReading.level_m)
                                  .where(ProbeReading.pool == job.pool, ProbeReading.superseded.is_(False),
                                         ProbeReading.minute >= start, ProbeReading.minute <= end)
                                  .order_by(ProbeReading.minute)).all()
                arrive = events_from_readings([(minute, level) for minute, level in rows], job.threshold_m)
                stored = db.scalars(select(HighEvent)
                                    .where(HighEvent.pool == job.pool, HighEvent.state == 'active',
                                           HighEvent.start_at <= end, HighEvent.end_at >= start)).all()
                old = [{'id': item.id, 'start': item.start_at, 'end': item.end_at, 'minutes': item.minutes,
                        'peak_level': item.peak_level, 'peak_at': item.peak_at} for item in stored]
                plan.append(diff_events(old, arrive))
    except Exception as error:
        log.exception('Recalc %s failed', job_id)
        failure = f'重算失败：{error}'[:500]
    with Session.begin() as db:
        job = db.get(RecalcJob, job_id, with_for_update=True)
        if job is None or job.attempt != attempt or job.status != Task.RUNNING:
            return
        job.lease_until = None
        job.finished_at = now()
        if failure:
            job.status = Task.FAILED
            job.error = failure
            record(db, 'worker', 'recalc.failed', job.id, reason=failure)
            return
        state = db.get(PoolState, job.pool, with_for_update=True)
        if state is None or state.epoch != job.epoch:
            # 重算期间读数或警戒线又变过，本次结果作废，等更新纪元的任务。
            job.status = Task.STALE
            record(db, 'worker', 'recalc.stale', job.id, pool=job.pool, epoch=job.epoch)
            return
        removed = added = 0
        for retire, arrive in plan:
            for item in retire:
                event = db.get(HighEvent, item['id'], with_for_update=True)
                if event and event.state == 'active':
                    event.state = 'superseded'
                    event.superseded_at = now()
                    removed += 1
            for item in arrive:
                db.add(HighEvent(pool=job.pool, start_at=item['start'], end_at=item['end'],
                                 minutes=item['minutes'], peak_level=item['peak_level'],
                                 peak_at=item['peak_at'], threshold_m=job.threshold_m, epoch=job.epoch))
                added += 1
        job.status = Task.DONE
        record(db, 'worker', 'recalc.done', job.id, pool=job.pool, epoch=job.epoch, removed=removed, added=added)
