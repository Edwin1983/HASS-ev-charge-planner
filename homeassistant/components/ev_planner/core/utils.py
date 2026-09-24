from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Amsterdam")


def parse_datetime(value):

    return datetime.fromisoformat(value)


def clamp(value, minimum, maximum):

    return max(
        minimum,
        min(
            maximum,
            value
        )
    )


def hour_key(dt):

    return dt.replace(
        minute=0,
        second=0,
        microsecond=0
    )


def overlap(start1, end1, start2, end2):

    return max(
        start1,
        start2
    ) < min(
        end1,
        end2
    )