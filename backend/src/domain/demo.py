"""澄湾会展园 RW-02 的固定晨检台账与分钟回传演示批次。"""

import csv
import io
from datetime import datetime, timedelta

_LEVELS = (0.83, 0.87, 0.89, None, 0.94, 1.02, 1.06, 1.13, 1.23, 1.25,
           1.35, 1.37, 1.44, 1.51, 1.59, 1.65, 1.72, 1.76, 1.83, 1.91)

# 11 月 13 日 23:50 起每分钟的液位（北京时间），00:22–00:25 通讯中断缺测。
# 23:58 起越线，00:07 峰值 2.34 m，00:21 回落到线；00:26–00:33 又一段越线但不足十分钟。
_PROBE_LEVELS = (
    (1.98, 1.99, 2.00, 2.01, 2.02, 2.03, 2.04, 2.04),          # 23:50–23:57
    (2.06, 2.08, 2.11, 2.14, 2.17, 2.20, 2.24, 2.27,           # 23:58–00:05
     2.30, 2.34, 2.33, 2.31, 2.28, 2.25, 2.22, 2.19,           # 00:06–00:13
     2.16, 2.13, 2.11, 2.09, 2.08, 2.07, 2.06, 2.05),          # 00:14–00:21
    None,                                                       # 00:22–00:25 缺测
    (2.06, 2.08, 2.09, 2.10, 2.09, 2.08, 2.07, 2.06),          # 00:26–00:33
    (2.03, 2.01, 1.99, 1.97, 1.95, 1.93, 1.91),                # 00:34–00:40
)
_PROBE_START = datetime(2026, 11, 13, 23, 50)


def demo_rows() -> list[dict]:
    return [
        {"reservoir_id": "RW-02", "reading_date": f"2026-11-{day:02d}", "level_m": level}
        for day, level in enumerate(_LEVELS, start=1)
        if level is not None
    ]


def probe_demo_rows() -> list[dict]:
    rows = []
    minute = _PROBE_START
    for group in _PROBE_LEVELS:
        if group is None:
            minute += timedelta(minutes=4)
            continue
        for level in group:
            rows.append({"reservoir_id": "RW-02", "reading_at": minute.strftime("%Y-%m-%d %H:%M"),
                         "level_m": level, "revision": 0})
            minute += timedelta(minutes=1)
    return rows


def probe_demo_csv() -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=["reservoir_id", "reading_at", "level_m", "revision"])
    writer.writeheader()
    writer.writerows(probe_demo_rows())
    return "\ufeff" + buffer.getvalue()
