from src.utils.time import iso


def dataset(item):
    return {'id': item.id, 'name': item.name, 'filename': item.filename, 'row_count': item.row_count,
            'checksum': item.checksum, 'author': item.author, 'created_at': iso(item.created_at)}


def run(item, detail=False):
    item_view = {'id': item.id, 'dataset_id': item.dataset_id, 'name': item.name, 'status': item.status,
                 'author': item.author, 'review': item.review, 'decision': item.decision,
                 'created_at': iso(item.created_at), 'finished_at': iso(item.finished_at),
                 'scheduled_at': iso(item.scheduled_at), 'schedule_id': item.schedule_id,
                 'has_export': bool(item.export_file), 'error': item.error}
    if detail:
        item_view.update(params=item.params, result=item.result, review_note=item.review_note)
    return item_view


def schedule(item):
    from src.domain.spec import SPEC
    return {'id': item.id, 'name': item.name, 'dataset_id': item.dataset_id, 'recipient': item.recipient,
            'next_at': iso(item.next_at), 'repeat_daily': item.repeat_daily, 'enabled': item.enabled,
            'next_report_day': item.params.get(SPEC['schedule_date_param']) if item.enabled else None}


def delivery(item):
    return {'id': item.id, 'run_id': item.run_id, 'recipient': item.recipient,
            'state': item.state, 'detail': item.detail, 'created_at': iso(item.created_at)}


def pool(item, active_events):
    return {'pool': item.pool, 'threshold_m': item.threshold_m, 'epoch': item.epoch,
            'active_events': active_events, 'updated_at': iso(item.updated_at)}


def event(item):
    return {'id': item.id, 'pool': item.pool, 'start_at': iso(item.start_at), 'end_at': iso(item.end_at),
            'minutes': item.minutes, 'peak_level': item.peak_level, 'peak_at': iso(item.peak_at),
            'threshold_m': item.threshold_m, 'state': item.state, 'epoch': item.epoch,
            'created_at': iso(item.created_at), 'superseded_at': iso(item.superseded_at)}


def reading(item, batch_name):
    return {'minute': iso(item.minute), 'level_m': item.level_m, 'revision': item.revision,
            'superseded': item.superseded, 'batch_id': item.batch_id, 'batch_name': batch_name}


def probe_batch(item):
    return {'id': item.id, 'name': item.name, 'filename': item.filename, 'row_count': item.row_count,
            'inserted': item.inserted, 'kept': item.kept, 'skipped': item.skipped,
            'checksum': item.checksum, 'author': item.author, 'created_at': iso(item.created_at)}


def recalc(item):
    return {'id': item.id, 'pool': item.pool, 'epoch': item.epoch, 'threshold_m': item.threshold_m,
            'windows': item.windows, 'status': item.status, 'attempt': item.attempt,
            'error': item.error, 'created_at': iso(item.created_at), 'finished_at': iso(item.finished_at)}
