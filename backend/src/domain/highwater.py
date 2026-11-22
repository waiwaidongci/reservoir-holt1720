"""探头分钟读数归集高位事件的纯计算口径。

输入假定已是当前有效读数（同池同一分钟只取修订号最大者，由服务层保证）。
连续达到警戒线满十分钟才记一次高位事件；缺测分钟断开连续性；
跨午夜按分钟时间戳自然连续，不按日历日切分。
"""

from datetime import timedelta

MINUTE = timedelta(minutes=1)
MIN_MINUTES = 10


def events_from_readings(readings, threshold, min_minutes=MIN_MINUTES):
    """把按分钟升序的有效读数 [(minute, level)] 归集成高位事件列表。"""
    events = []
    run = []
    previous = None
    for minute, level in readings:
        if run and (minute - previous != MINUTE or level < threshold):
            if len(run) >= min_minutes:
                events.append(_event(run))
            run = []
        if level >= threshold:
            run.append((minute, level))
        previous = minute
    if len(run) >= min_minutes:
        events.append(_event(run))
    return events


def _event(run):
    peak_level = max(level for _, level in run)
    peak_at = next(minute for minute, level in run if level == peak_level)
    return {
        "start": run[0][0],
        "end": run[-1][0],
        "minutes": len(run),
        "peak_level": peak_level,
        "peak_at": peak_at,
    }


def affected_windows(have_minutes, changed):
    """把受影响分钟扩展成连续有读数的区段。

    区段边界必为缺测分钟（或池内数据尽头），事件不可能跨越缺测，
    因此只重算这些区段即可覆盖合并与拆分的全部影响。
    """
    windows = []
    for minute in sorted(changed):
        if minute not in have_minutes:
            continue
        start = minute
        while start - MINUTE in have_minutes:
            start -= MINUTE
        end = minute
        while end + MINUTE in have_minutes:
            end += MINUTE
        if windows and start <= windows[-1][1] + MINUTE:
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end])
    return [(start, end) for start, end in windows]


def event_key(event):
    return (event["start"], event["end"], event["minutes"], event["peak_level"], event["peak_at"])


def diff_events(old, new):
    """比较区段内新旧事件，返回 (应撤下的旧事件, 应登记的新事件)。

    重算结果与库内一致时两边都为空，重跑同批不会重复计时。
    """
    old_keys = {event_key(item) for item in old}
    new_keys = {event_key(item) for item in new}
    retire = [item for item in old if event_key(item) not in new_keys]
    arrive = [item for item in new if event_key(item) not in old_keys]
    return retire, arrive
