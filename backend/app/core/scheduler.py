import re
from typing import Union

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

scheduler = AsyncIOScheduler(
    job_defaults={"misfire_grace_time": 3600, "coalesce": True},
)

_INTERVAL_RE = re.compile(r"^([1-9][0-9]*)(h|d|m)$")


def build_trigger(
    schedule_type: str, schedule_value: str
) -> Union[CronTrigger, IntervalTrigger]:
    if schedule_type == "cron":
        return CronTrigger.from_crontab(schedule_value)
    if schedule_type == "interval":
        m = _INTERVAL_RE.match(schedule_value)
        if not m:
            raise ValueError(f"Invalid interval value: {schedule_value!r}")
        n, unit = int(m.group(1)), m.group(2)
        if unit == "h":
            return IntervalTrigger(hours=n)
        if unit == "d":
            return IntervalTrigger(days=n)
        return IntervalTrigger(minutes=n)
    raise ValueError(f"Unknown schedule_type: {schedule_type!r}")


async def start_scheduler() -> None:
    pass


async def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
