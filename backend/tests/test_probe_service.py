"""导入→重算→高位事件的服务层集成测试（SQLite 文件库）。"""

import os
import tempfile
import unittest

_DB = os.path.join(tempfile.gettempdir(), 'probe_service_test.db')
os.environ['DATABASE_URL'] = f'sqlite:///{_DB}'
os.environ['JWT_SECRET'] = 'integration-test-only'

from src.config.database import Base, Session, engine  # noqa: E402
from src.constants.enums import Task  # noqa: E402
from src.domain.demo import probe_demo_csv  # noqa: E402
from src.models import identity, probe, source, jobs  # noqa: E402,F401
from src.models.probe import HighEvent, PoolState, RecalcJob  # noqa: E402
from src.services.probe import (claim_recalc, execute_recalc, import_batch,  # noqa: E402
                                parse_probe_rows)

BEIJING_TO_UTC = 8


def utc(day, hour, minute):
    """北京时间（11 月）转库存 UTC。"""
    from datetime import datetime, timedelta
    return datetime(2026, 11, day, hour, minute) - timedelta(hours=BEIJING_TO_UTC)


def drain():
    ran = []
    while True:
        task = claim_recalc()
        if not task:
            return ran
        execute_recalc(task)
        ran.append(task[0])


def active_events(db):
    return db.scalars(__import__('sqlalchemy').select(HighEvent)
                      .where(HighEvent.state == 'active').order_by(HighEvent.start_at)).all()


class ProbeServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.path.exists(_DB):
            os.remove(_DB)
        Base.metadata.create_all(engine)

    def setUp(self):
        with Session.begin() as db:
            for model in (HighEvent, RecalcJob, PoolState, probe.ProbeReading, probe.ProbeBatch,
                          identity.Audit):
                db.query(model).delete()

    def import_csv(self, name, text, threshold=None):
        with Session.begin() as db:
            batch, created, stats, jobs = import_batch(db, name, f'{name}.csv',
                                                       parse_probe_rows(text), 'test', threshold=threshold)
            return batch.id, created, stats, [job.id for job in jobs]

    def test_full_lifecycle_merge_split_replay(self):
        # 批次 A：跨午夜回传，23:58–00:21 越线，00:22–00:25 缺测，00:26–00:33 又越线但不足十分钟。
        _, created, stats, job_ids = self.import_csv('批次A', probe_demo_csv())
        self.assertTrue(created)
        self.assertEqual((stats['inserted'], stats['kept'], stats['skipped']), (47, 0, 0))
        self.assertEqual(len(job_ids), 1)
        drain()
        with Session() as db:
            (event,) = active_events(db)
            self.assertEqual((event.start_at, event.end_at), (utc(13, 23, 58), utc(14, 0, 21)))
            self.assertEqual(event.minutes, 24)
            self.assertEqual((event.peak_level, event.peak_at), (2.34, utc(14, 0, 7)))
            self.assertEqual(db.get(RecalcJob, job_ids[0]).status, Task.DONE)

        # 批次 B：乱序补传缺测的 00:22–00:25，两段越线合并成一个 36 分钟事件。
        backfill = ('reservoir_id,reading_at,level_m,revision\n'
                    'RW-02,2026-11-14 00:22,2.12,0\nRW-02,2026-11-14 00:23,2.09,0\n'
                    'RW-02,2026-11-14 00:24,2.07,0\nRW-02,2026-11-14 00:25,2.05,0\n')
        _, created, stats, _ = self.import_csv('批次B', backfill)
        self.assertTrue(created)
        self.assertEqual(stats['inserted'], 4)
        drain()
        with Session() as db:
            (event,) = active_events(db)
            self.assertEqual((event.start_at, event.end_at, event.minutes),
                             (utc(13, 23, 58), utc(14, 0, 33), 36))
            superseded = db.scalars(__import__('sqlalchemy').select(HighEvent)
                                    .where(HighEvent.state == 'superseded')).all()
            self.assertEqual(len(superseded), 1)  # 24 分钟版本留档

        # 批次 C：更正 00:10 为线下（更大修订号），事件拆成 12 + 23 分钟两段。
        correction = 'reservoir_id,reading_at,level_m,revision\nRW-02,2026-11-14 00:10,1.87,1\n'
        _, created, stats, _ = self.import_csv('批次C', correction)
        self.assertTrue(created)
        self.assertEqual(stats['inserted'], 1)
        drain()
        with Session() as db:
            events = active_events(db)
            self.assertEqual([(item.start_at, item.minutes) for item in events],
                             [(utc(13, 23, 58), 12), (utc(14, 0, 11), 23)])
            # 旧修订留档核对，当前有效读数为修订 1。
            readings = db.scalars(__import__('sqlalchemy').select(probe.ProbeReading)
                                  .where(probe.ProbeReading.minute == utc(14, 0, 10))
                                  .order_by(probe.ProbeReading.revision)).all()
            self.assertEqual([(item.revision, item.level_m, item.superseded) for item in readings],
                             [(0, 2.28, True), (1, 1.87, False)])

        # 重跑同批：指纹命中直接短路，不排重算、不重复计时。
        _, created, stats, job_ids = self.import_csv('批次A', probe_demo_csv())
        self.assertFalse(created)
        self.assertIsNone(stats)
        self.assertEqual(job_ids, [])
        with Session() as db:
            self.assertEqual(len(active_events(db)), 2)

    def test_stale_recalc_cannot_overwrite_newer_epoch(self):
        self.import_csv('批次A', probe_demo_csv())
        # 第一批的重算已被领取（running），尚未完成。
        first_task = claim_recalc()
        self.assertIsNotNone(first_task)
        backfill = ('reservoir_id,reading_at,level_m,revision\n'
                    'RW-02,2026-11-14 00:22,2.12,0\nRW-02,2026-11-14 00:23,2.09,0\n'
                    'RW-02,2026-11-14 00:24,2.07,0\nRW-02,2026-11-14 00:25,2.05,0\n')
        self.import_csv('批次B', backfill)
        with Session() as db:
            first, second = db.scalars(__import__('sqlalchemy').select(RecalcJob)
                                       .order_by(RecalcJob.id)).all()
            self.assertEqual((first.epoch, second.epoch), (1, 2))
            second_id = second.id
        # 较早的重算晚完成：纪元已推进，结果作废，不切换事件。
        execute_recalc(first_task)
        with Session() as db:
            self.assertEqual(db.get(RecalcJob, first_task[0]).status, Task.STALE)
            self.assertEqual(active_events(db), [])
        execute_recalc(claim_recalc())
        with Session() as db:
            self.assertEqual(db.get(RecalcJob, second_id).status, Task.DONE)
            (event,) = active_events(db)
            self.assertEqual(event.minutes, 36)

    def test_revision_conflict_is_rejected(self):
        self.import_csv('批次A', probe_demo_csv())
        conflict = 'reservoir_id,reading_at,level_m,revision\nRW-02,2026-11-14 00:10,1.50,0\n'
        with self.assertRaises(ValueError):
            self.import_csv('冲突批', conflict)

    def test_threshold_change_triggers_full_recalc(self):
        self.import_csv('批次A', probe_demo_csv())
        drain()
        with Session() as db:
            self.assertEqual(len(active_events(db)), 1)
        # 警戒线调到 2.30 m：只剩 00:06–00:09 连续 4 分钟越线，不足十分钟，事件消失。
        self.import_csv('批次B', 'reservoir_id,reading_at,level_m,revision\n'
                        'RW-02,2026-11-14 00:41,1.90,0\n', threshold=2.30)
        drain()
        with Session() as db:
            self.assertEqual(active_events(db), [])
            self.assertEqual(db.get(PoolState, 'RW-02').threshold_m, 2.30)


if __name__ == '__main__':
    unittest.main()
