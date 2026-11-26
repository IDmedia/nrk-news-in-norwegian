"""Decides whether a scheduled pipeline should build the feeds. Exits 0 to run, 1 to skip.

The pipeline is scheduled every 15 minutes, but that is only needed while the regional
bulletins air: weekdays between about 06:00 and 16:30, Oslo time (markers are sometimes
added after the show ends, hence until 18:00). Otherwise only the hourly national bulletin
can change, so one run an hour is enough: the one in the :15 slot, after the bulletin
at :00 (about 3 minutes long) has finished.

Uses the pipeline's creation time, so a job that waited for a runner keeps its slot.
Runs that weren't scheduled (started by hand) always build.
"""
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

if os.environ.get("CI_PIPELINE_SOURCE") != "schedule":
    sys.exit(0)

created = os.environ.get("CI_PIPELINE_CREATED_AT")
now = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else datetime.now(timezone.utc)
local = now.astimezone(ZoneInfo("Europe/Oslo"))

regional_hours = local.weekday() < 5 and 6 <= local.hour < 18
sys.exit(0 if regional_hours or 15 <= local.minute < 30 else 1)
