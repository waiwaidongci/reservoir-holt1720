"""高位事件 API 端到端测试：登录 → 导入 → 重算 → 查询 → 幂等重放。

注意：数据库引擎在首次导入 src 模块时绑定。与其它测试同进程运行时，
本模块在启动应用前清空所有表，保证种子数据从干净状态写入。
"""

import os
import tempfile
import unittest

_DB = os.path.join(tempfile.gettempdir(), 'probe_api_test.db')
os.environ['DATABASE_URL'] = f'sqlite:///{_DB}'
os.environ['JWT_SECRET'] = 'api-test-only'
os.environ['SEED_DEMO'] = 'true'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:1/0'  # 不可达：wake() 应静默降级

from fastapi.testclient import TestClient  # noqa: E402
from src.config.database import Base, Session, engine  # noqa: E402
from src.main import app  # noqa: E402
from src.services.probe import claim_recalc, execute_recalc  # noqa: E402


def drain():
    while True:
        task = claim_recalc()
        if not task:
            return
        execute_recalc(task)


class ProbeApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine)
        with Session.begin() as db:
            for table in reversed(Base.metadata.sorted_tables):
                db.execute(table.delete())
        cls.client = TestClient(app)
        cls.client.__enter__()
        token = cls.client.post('/auth/login', json={'username': 'analyst', 'password': 'analyst-demo-2611'}).json()['token']
        cls.auth = {'Authorization': f'Bearer {token}'}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_handover_flow(self):
        client, auth = self.client, self.auth

        # 种子批次已建，重算后交班页能看到跨午夜事件。
        drain()
        pools = client.get('/pools', headers=auth).json()
        self.assertEqual([(item['pool'], item['active_events']) for item in pools], [('RW-02', 1)])
        events = client.get('/events', headers=auth).json()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual((event['minutes'], event['peak_level']), (24, 2.34))
        self.assertEqual(event['start_at'], '2026-11-13T15:58:00+00:00')  # 北京 23:58

        detail = client.get(f"/events/{event['id']}", headers=auth).json()
        self.assertEqual(len(detail['readings']), 24)
        self.assertTrue(all(not row['superseded'] for row in detail['readings']))

        # 乱序补传 00:22–00:25：事件合并为 36 分钟。
        backfill = ('reservoir_id,reading_at,level_m,revision\n'
                    'RW-02,2026-11-14 00:22,2.12,0\nRW-02,2026-11-14 00:23,2.09,0\n'
                    'RW-02,2026-11-14 00:24,2.07,0\nRW-02,2026-11-14 00:25,2.05,0\n')
        result = client.post('/probe/batches', headers=auth, json={
            'name': '补传批', 'filename': 'backfill.csv', 'csv_text': backfill}).json()
        self.assertTrue(result['created'])
        self.assertEqual(result['stats']['inserted'], 4)
        self.assertEqual(len(result['jobs']), 1)
        drain()
        (merged,) = client.get('/events', headers=auth).json()
        self.assertEqual(merged['minutes'], 36)

        # 更正 00:10 到线下：事件拆分。
        correction = 'reservoir_id,reading_at,level_m,revision\nRW-02,2026-11-14 00:10,1.87,1\n'
        result = client.post('/probe/batches', headers=auth, json={
            'name': '更正批', 'filename': 'correction.csv', 'csv_text': correction}).json()
        self.assertTrue(result['created'])
        drain()
        events = client.get('/events', headers=auth).json()
        self.assertEqual(sorted(item['minutes'] for item in events), [12, 23])

        # 区间内峰值修正：00:15 改为 2.40（仍在越线），事件跨度不变但峰值切换，旧读数留档核对。
        peak_fix = 'reservoir_id,reading_at,level_m,revision\nRW-02,2026-11-14 00:15,2.40,1\n'
        client.post('/probe/batches', headers=auth, json={
            'name': '峰值修正批', 'filename': 'peak.csv', 'csv_text': peak_fix})
        drain()
        events = client.get('/events', headers=auth).json()
        self.assertEqual(sorted(item['minutes'] for item in events), [12, 23])
        second = next(item for item in events if item['minutes'] == 23)
        self.assertEqual((second['peak_level'], second['peak_at']), (2.40, '2026-11-13T16:15:00+00:00'))
        detail = client.get(f"/events/{second['id']}", headers=auth).json()
        at_quarter = [row for row in detail['readings'] if row['minute'] == '2026-11-13T16:15:00+00:00']
        self.assertEqual(sorted((row['revision'], row['level_m'], row['superseded']) for row in at_quarter),
                         [(0, 2.13, True), (1, 2.40, False)])

        # 重跑同批：指纹短路，不重复计时。
        again = client.post('/probe/batches', headers=auth, json={
            'name': '峰值修正批', 'filename': 'peak.csv', 'csv_text': peak_fix}).json()
        self.assertFalse(again['created'])
        self.assertEqual(again['jobs'], [])
        self.assertEqual(len(client.get('/events', headers=auth).json()), 2)

        # 批次、任务与模板接口。
        batches = client.get('/probe/batches', headers=auth).json()
        self.assertEqual(len(batches), 4)
        jobs = client.get('/probe/jobs', headers=auth).json()
        self.assertTrue(all(item['status'] == 'done' for item in jobs))
        template = client.get('/probe/template', headers=auth)
        self.assertEqual(template.status_code, 200)
        self.assertIn('reading_at', template.text)

        # 未登录不可见。
        self.assertEqual(client.get('/events').status_code, 401)


if __name__ == '__main__':
    unittest.main()
