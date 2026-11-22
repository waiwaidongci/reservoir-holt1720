import unittest
from datetime import datetime, timedelta

from src.domain.highwater import (MINUTE, affected_windows, diff_events,
                                  events_from_readings)

BASE = datetime(2026, 11, 13, 23, 50)
THRESHOLD = 2.05


def span(start, count):
    """从 start 起 count 个连续分钟。"""
    return [start + i * MINUTE for i in range(count)]


def readings(minutes, level):
    return [(minute, level) for minute in minutes]


class EventsFromReadingsTest(unittest.TestCase):
    def test_nine_high_minutes_do_not_make_an_event(self):
        self.assertEqual(events_from_readings(readings(span(BASE, 9), 2.3), THRESHOLD), [])

    def test_ten_high_minutes_make_one_event(self):
        minutes = span(BASE, 10)
        (event,) = events_from_readings(readings(minutes, 2.3), THRESHOLD)
        self.assertEqual(event["start"], BASE)
        self.assertEqual(event["end"], BASE + 9 * MINUTE)
        self.assertEqual(event["minutes"], 10)
        self.assertEqual(event["peak_level"], 2.3)
        self.assertEqual(event["peak_at"], BASE)

    def test_threshold_is_inclusive(self):
        (event,) = events_from_readings(readings(span(BASE, 10), THRESHOLD), THRESHOLD)
        self.assertEqual(event["minutes"], 10)

    def test_missing_minute_breaks_the_run(self):
        minutes = span(BASE, 6) + span(BASE + 7 * MINUTE, 6)  # 第 7 分钟缺测
        self.assertEqual(events_from_readings(readings(minutes, 2.3), THRESHOLD), [])

    def test_low_minute_breaks_the_run(self):
        rows = readings(span(BASE, 6), 2.3) + [(BASE + 6 * MINUTE, 1.9)] + readings(span(BASE + 7 * MINUTE, 6), 2.3)
        self.assertEqual(events_from_readings(rows, THRESHOLD), [])

    def test_event_crosses_midnight(self):
        minutes = span(datetime(2026, 11, 13, 23, 55), 12)  # 23:55 跨到次日 00:06
        (event,) = events_from_readings(readings(minutes, 2.3), THRESHOLD)
        self.assertEqual(event["start"], datetime(2026, 11, 13, 23, 55))
        self.assertEqual(event["end"], datetime(2026, 11, 14, 0, 6))
        self.assertEqual(event["minutes"], 12)

    def test_peak_uses_earliest_minute_on_tie(self):
        rows = [(BASE, 2.1), (BASE + MINUTE, 2.4), (BASE + 2 * MINUTE, 2.4)] + readings(span(BASE + 3 * MINUTE, 7), 2.2)
        (event,) = events_from_readings(rows, THRESHOLD)
        self.assertEqual(event["peak_level"], 2.4)
        self.assertEqual(event["peak_at"], BASE + MINUTE)

    def test_two_separate_events(self):
        minutes = span(BASE, 11) + span(BASE + 20 * MINUTE, 12)
        events = events_from_readings(readings(minutes, 2.3), THRESHOLD)
        self.assertEqual([event["minutes"] for event in events], [11, 12])


class AffectedWindowsTest(unittest.TestCase):
    def test_window_extends_to_segment_edges(self):
        have = set(span(BASE, 30))
        self.assertEqual(affected_windows(have, {BASE + 10 * MINUTE}), [(BASE, BASE + 29 * MINUTE)])

    def test_window_stops_at_missing_minutes(self):
        have = set(span(BASE, 10)) | set(span(BASE + 12 * MINUTE, 10))  # 第 11–12 分钟缺测
        self.assertEqual(affected_windows(have, {BASE + 5 * MINUTE}), [(BASE, BASE + 9 * MINUTE)])

    def test_overlapping_windows_merge(self):
        have = set(span(BASE, 30))
        changed = {BASE + 5 * MINUTE, BASE + 20 * MINUTE}
        self.assertEqual(affected_windows(have, changed), [(BASE, BASE + 29 * MINUTE)])

    def test_separate_segments_stay_separate(self):
        have = set(span(BASE, 10)) | set(span(BASE + 20 * MINUTE, 10))
        changed = {BASE + 2 * MINUTE, BASE + 25 * MINUTE}
        windows = affected_windows(have, changed)
        self.assertEqual(windows, [(BASE, BASE + 9 * MINUTE), (BASE + 20 * MINUTE, BASE + 29 * MINUTE)])


class DiffEventsTest(unittest.TestCase):
    def test_identical_results_have_no_changes(self):
        events = [{"start": BASE, "end": BASE + 9 * MINUTE, "minutes": 10, "peak_level": 2.3, "peak_at": BASE}]
        retire, arrive = diff_events(events, [dict(event) for event in events])
        self.assertEqual((retire, arrive), ([], []))

    def test_changed_event_is_retired_and_added(self):
        old = [{"start": BASE, "end": BASE + 9 * MINUTE, "minutes": 10, "peak_level": 2.3, "peak_at": BASE}]
        new = [{"start": BASE, "end": BASE + 11 * MINUTE, "minutes": 12, "peak_level": 2.3, "peak_at": BASE}]
        retire, arrive = diff_events(old, new)
        self.assertEqual(retire, old)
        self.assertEqual(arrive, new)


class BackfillFlowTest(unittest.TestCase):
    """乱序补传合并事件、更正拆分事件的完整口径回放。"""

    def setUp(self):
        self.current = {}  # minute -> (revision, level)

    def apply(self, rows):
        changed = set()
        for minute, revision, level in rows:
            existing = self.current.get(minute)
            if existing and existing[0] >= revision:
                continue
            self.current[minute] = (revision, level)
            changed.add(minute)
        events = []
        for start, end in affected_windows(set(self.current), changed):
            segment = [(minute, self.current[minute][1]) for minute in span(start, round((end - start) / MINUTE) + 1)]
            events.extend(events_from_readings(segment, THRESHOLD))
        return sorted(events, key=lambda event: event["start"])

    def test_backfill_merges_and_correction_splits(self):
        evening = datetime(2026, 11, 13, 23, 58)
        morning = datetime(2026, 11, 14, 0, 26)
        batch_a = [(minute, 0, 2.3) for minute in span(evening, 24) + span(morning, 8)]
        events = self.apply(batch_a)
        self.assertEqual([(event["start"], event["minutes"]) for event in events], [(evening, 24)])

        backfill = [(datetime(2026, 11, 14, 0, 22) + i * MINUTE, 0, 2.1) for i in range(4)]
        events = self.apply(backfill)
        self.assertEqual([(event["start"], event["end"], event["minutes"]) for event in events],
                         [(evening, morning + 7 * MINUTE, 36)])

        correction = [(datetime(2026, 11, 14, 0, 10), 1, 1.87)]
        events = self.apply(correction)
        self.assertEqual([(event["start"], event["minutes"]) for event in events],
                         [(evening, 12), (datetime(2026, 11, 14, 0, 11), 23)])

    def test_replaying_same_batch_changes_nothing(self):
        batch = [(minute, 0, 2.3) for minute in span(BASE, 15)]
        first = self.apply(batch)
        self.assertEqual(len(first), 1)
        # 重放同批：所有分钟修订号不变，没有受影响分钟，无需重算。
        changed = set()
        for minute, revision, level in batch:
            existing = self.current.get(minute)
            if existing and existing[0] >= revision:
                continue
            changed.add(minute)
        self.assertEqual(changed, set())
        self.assertEqual(affected_windows(set(self.current), changed), [])
        # 即使强制重算同一区段，diff 也为空，不会重复计时。
        segment = [(minute, self.current[minute][1]) for minute in span(BASE, 15)]
        retire, arrive = diff_events(first, events_from_readings(segment, THRESHOLD))
        self.assertEqual((retire, arrive), ([], []))


if __name__ == "__main__":
    unittest.main()
