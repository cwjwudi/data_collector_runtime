import datetime
import calendar

# 全局常量（避免重复创建）
_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


# def fast_dt_to_date_and_time(dt):
#     """极速版：无任何校验，仅计算秒数"""
#     return int((dt - _EPOCH).total_seconds())

def fast_dt_to_date_and_time(dt):
    """
    将 datetime 转为 DATE_AND_TIME 的 32 位秒数。
    - 如果 dt 是 naive，视为 UTC。
    - 如果 dt 是 aware，必须为 UTC。
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    elif dt.utcoffset() != datetime.timedelta(0):
        raise ValueError("Only UTC datetime is supported")
    return int((dt - _EPOCH).total_seconds())

def fast_utc_dt_to_timestamp(dt):
    return calendar.timegm(dt.timetuple())

def datetime_to_date_and_time(dt: datetime.datetime) -> int:
    if not isinstance(dt, datetime.datetime):
        raise TypeError("Input must be a datetime.datetime object")
    if dt.tzinfo is None:
        raise ValueError("Input datetime must be timezone-aware")
    if dt.utcoffset() != datetime.timedelta(0):
        raise ValueError("Input datetime must be in UTC")

    epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    seconds = int((dt - epoch).total_seconds())

    if seconds < 0 or seconds > 0xFFFFFFFF:
        raise ValueError("Out of DATE_AND_TIME range")
    return seconds
