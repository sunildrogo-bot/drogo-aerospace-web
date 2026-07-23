"""
load_monitor.py — Lightweight, in-process portal load tracking.

Honest scope, worth reading before trusting the numbers this produces:
this tracks requests handled by THIS PROCESS only, using plain in-memory
structures (a deque + a dict). If the app ever runs with multiple worker
processes — e.g. `gunicorn -w 4 app:app`, which is normal for a real
production deployment — each worker has its own separate copy of these
structures. The dashboard would then only reflect whichever single
worker happened to handle the /api/dashboard/live-load request itself,
not the true combined load across every worker. For accurate
multi-process monitoring, this would need to move to a shared store
(Redis, or write-heavy rows in the database) instead of in-memory state.
For a single-process deployment (`python app.py`, or one gunicorn
worker), what's here is accurate.

This is also not a replacement for a real APM/monitoring tool (Datadog,
New Relic, etc.) if this app ever needs production-grade observability
at real scale — it's a lightweight, good-enough live indicator for a
small internal tool.
"""
import time
import threading
from collections import deque

_lock = threading.Lock()
_request_log = deque(maxlen=3000)   # (timestamp, duration_ms) for recent requests
_active_users = {}                  # user_id -> last_seen unix timestamp

ACTIVE_WINDOW_SECONDS = 180  # "active right now" = made a request within the last 3 minutes

# Rolling history for the live chart — retained for 10 hours, then
# dropped, per the requirement that older data isn't needed. This is a
# SEPARATE structure from _request_log above (which only needs the last
# 60 seconds to compute requests/min) — deliberately unbounded by count
# and pruned purely by time instead, so retention is exactly "10 hours,"
# not "however many requests happen to fit in a fixed-size buffer."
SNAPSHOT_RETENTION_SECONDS = 10 * 3600
_snapshot_history = deque()


def record_request(duration_ms, user_id=None, count_toward_load=True):
    now = time.time()
    with _lock:
        if count_toward_load:
            _request_log.append((now, duration_ms))
        # User presence is tracked regardless — an admin quietly watching
        # the live chart (which polls this endpoint, not making any other
        # request) should still show up as "active," even though those
        # polls themselves are excluded from the request-rate count above.
        if user_id is not None:
            _active_users[user_id] = now


def _record_snapshot(stats):
    now = stats['timestamp']
    cutoff = now - SNAPSHOT_RETENTION_SECONDS
    with _lock:
        _snapshot_history.append(stats)
        while _snapshot_history and _snapshot_history[0]['timestamp'] < cutoff:
            _snapshot_history.popleft()


def get_snapshot_history(max_points=400):
    """The retained (up to 10h) history, downsampled to at most
    max_points. A true second-by-second 10-hour history could be tens of
    thousands of points — sending all of them on every page load/restore
    would be a large, slow payload and an SVG with as many path segments,
    for no visible difference at normal chart widths. Downsampling by
    even stride keeps the shape of the trend intact while keeping the
    payload light."""
    with _lock:
        history = list(_snapshot_history)
    if len(history) <= max_points:
        return history
    stride = len(history) / max_points
    return [history[int(i * stride)] for i in range(max_points)]


def get_live_stats():
    now = time.time()
    with _lock:
        recent = [d for (t, d) in _request_log if now - t <= 60]
        active_count = sum(1 for t in _active_users.values() if now - t <= ACTIVE_WINDOW_SECONDS)
        # Housekeeping — drop entries that have been stale a long while so
        # this dict doesn't grow forever across a long-running process.
        stale = [uid for uid, t in _active_users.items() if now - t > ACTIVE_WINDOW_SECONDS * 3]
        for uid in stale:
            del _active_users[uid]

    requests_per_minute = len(recent)
    avg_response_ms = round(sum(recent) / len(recent), 1) if recent else 0.0
    max_response_ms = round(max(recent), 1) if recent else 0.0

    # A simple, honest heuristic for a traffic-light load indicator — not
    # a scientific measurement, just rough thresholds on response time and
    # request volume. Tune these two lines if they don't match reality
    # once there's real usage data to compare against.
    if avg_response_ms > 800 or requests_per_minute > 120:
        level = 'high'
    elif avg_response_ms > 300 or requests_per_minute > 40:
        level = 'moderate'
    else:
        level = 'low'

    result = {
        'active_users_now': active_count,
        'requests_per_minute': requests_per_minute,
        'avg_response_ms': avg_response_ms,
        'max_response_ms': max_response_ms,
        'level': level,
        'timestamp': now,
    }
    _record_snapshot(result)
    return result
