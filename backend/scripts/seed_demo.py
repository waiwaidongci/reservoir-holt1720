import json
from sqlalchemy import func, select
from src.config.database import Session
from src.constants.enums import Role
from src.domain.demo import demo_rows, probe_demo_csv
from src.domain.spec import SPEC
from src.models.identity import User
from src.models.probe import ProbeBatch
from src.models.source import Dataset
from src.services.datasets import save
from src.services.probe import import_batch, parse_probe_rows
from src.utils.security import hash_password


def seed():
    with Session.begin() as db:
        for role in Role:
            if not db.get(User, role.value):
                db.add(User(username=role.value, role=role, password_hash=hash_password(f'{role.value}-demo-2611')))
        item, created = save(db, SPEC['default_dataset'], SPEC['sample_name'], demo_rows(), 'seed')
        probe_rows = parse_probe_rows(probe_demo_csv())
        batch, probe_created, stats, jobs = import_batch(
            db, 'RW-02 分钟回传 11-13 夜', SPEC['probe']['sample_name'], probe_rows, 'seed')
        return {'users': db.scalar(select(func.count()).select_from(User)),
                'datasets': db.scalar(select(func.count()).select_from(Dataset)),
                'seed_dataset_id': item.id, 'row_count': item.row_count, 'checksum': item.checksum,
                'created_dataset': created,
                'probe_batches': db.scalar(select(func.count()).select_from(ProbeBatch)),
                'probe_batch_id': batch.id, 'probe_created': probe_created,
                'probe_stats': stats, 'recalc_jobs': [job.id for job in jobs]}


if __name__ == '__main__':
    print(json.dumps(seed(), ensure_ascii=False))
