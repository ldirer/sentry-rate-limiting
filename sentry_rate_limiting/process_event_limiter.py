import logging
import traceback
from collections import defaultdict
from collections import deque
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from logging import LogRecord
from traceback import StackSummary
from types import TracebackType

from sentry_sdk.types import Event
from sentry_sdk.types import Hint

logger = logging.getLogger(__name__)


class PerProcessPerIssueEventLimiter:
    """Rate limit events per Sentry issue.

    This prevents high-volume errors from consuming the Sentry quota.
    Events are grouped by 'fingerprint' (proxy for an issue identifier), tracked in memory per process.
    """

    def __init__(
        self,
        rate_limit_window_minutes: int = 15,
        rate_limit_number_of_events: int = 100,
    ) -> None:
        self.recorded: defaultdict[str, deque[datetime]] = defaultdict(deque)
        self.rate_limit_window: timedelta = timedelta(minutes=rate_limit_window_minutes)
        self.rate_limit_number_of_events: int = rate_limit_number_of_events

    def should_rate_limit(self, event: Event, hint: Hint) -> bool:
        """Record the event, determine if it should be rate-limited.
        Rate limited events are discarded and do not count against the rate limit.

        This ensures ongoing issues keep flowing to Sentry at a steady, limited rate
        instead of being entirely silenced after hitting the limit.
        """
        fingerprint = build_event_fingerprint(event, hint)
        now = datetime.now(tz=timezone.utc)

        self.remove_old_records(now=now)
        fingerprint_timestamps = self.recorded[fingerprint]

        drop_event = len(fingerprint_timestamps) >= self.rate_limit_number_of_events
        if not drop_event:
            # Add event timestamp only if it's not being rate-limited.
            fingerprint_timestamps.append(now)

        return drop_event

    def remove_old_records(self, now: datetime) -> None:
        boundary = now - self.rate_limit_window
        for timestamps in self.recorded.values():
            while timestamps and timestamps[0] < boundary:
                timestamps.popleft()

    def before_send(self, event: Event, hint: Hint) -> Event | None:
        """This function lets us modify the event before sending it.
        Returning `None` causes the event to be dropped.

        https://docs.sentry.io/platforms/python/configuration/filtering/#filtering-error-events

        If an exception occurs here, it will be silently ignored and the event dropped.
        Need `debug=True` in sentry init to see the error in logs.
        (see rationale in last message of https://github.com/getsentry/sentry-python/issues/402)
        """
        # noinspection PyBroadException
        try:
            drop_event = self.should_rate_limit(event, hint)
        except Exception:
            logger.warning(
                "exception occurred in sentry before_send, ignoring rate limit",
                exc_info=True,
            )
            drop_event = False

        if drop_event:
            return None

        return event


def build_event_fingerprint(event: Event, hint: Hint) -> str:
    """Return a grouping identifier (or 'fingerprint') of the event.

    Sentry uses a 'fingerprint' of an event to decide how to group it with other events.
    Sentry's fingerprinting happens on the Sentry server, see:
    https://github.com/getsentry/sentry/blob/master/src/sentry/grouping/fingerprinting/__init__.py

    I was not sure how to reuse that, so settled for a simpler version that can be completed as needed.
    """
    if "exc_info" in hint:
        return _fingerprint_from_exc_info(hint)

    # in case of a logging.error(exc_info=True) we don't have `exc_info` in hint.
    if "threads" in event:
        return _fingerprint_from_threads(event)

    # sometimes we don't have any access to a stacktrace (if we didn't pass `exc_info=True`).
    if "log_record" in hint:
        return _fingerprint_from_log_record(hint)

    raise NotImplementedError("unhandled case in build_event_fingerprint")


def _fingerprint_from_exc_info(hint: Hint) -> str:
    exc_tb: TracebackType = hint.get("exc_info")[2]
    # extract *static* trace info.
    # there are subtle gotchas if relying on exc_tb.tb_frame instead (it is mutated as the execution continues)
    tb_summary: StackSummary = traceback.extract_tb(exc_tb)
    return "\n".join([f"{frame.filename}:{frame.lineno}" for frame in tb_summary])


def _fingerprint_from_threads(event: Event) -> str:
    if len(event["threads"]["values"]) > 1:
        # not sure when this happens, logging integration sets the event["threads"] directly with a single value
        raise NotImplementedError(
            "got multiple values in sentry 'threads' attribute! {}".format(event["threads"]["values"])
        )

    stacktrace = event["threads"]["values"][0]["stacktrace"]
    return "\n".join([f"{frame['abs_path']}:{frame['lineno']}" for frame in stacktrace["frames"]])


def _fingerprint_from_log_record(hint: Hint) -> str:
    log_record: LogRecord = hint["log_record"]
    return f"LogRecord {log_record.pathname}:{log_record.lineno} {log_record.msg}"
