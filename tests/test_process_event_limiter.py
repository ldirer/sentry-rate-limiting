import contextlib
import logging
import re
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
import sentry_sdk
from freezegun import freeze_time
from sentry_sdk.integrations.logging import EventHandler
from sentry_sdk.utils import event_from_exception

from sentry_rate_limiting.process_event_limiter import PerProcessPerIssueEventLimiter
from sentry_rate_limiting.process_event_limiter import build_event_fingerprint


def f_raise_1():
    raise ValueError("test 1")


def f_raise_2():
    raise ValueError("test 2")


class EventCaptureHelper:
    def __init__(self, monkeypatch):
        self._fixture_event = None
        self._fixture_hint = None
        sentry_sdk.init(dsn="http://testdsn@localhost/1")
        # I tried a mock client at first, but ended up having to patch too many things (methods, configuration...)
        self.client = sentry_sdk.get_client()

        def record_event(event, hint):
            self._fixture_event = event
            self._fixture_hint = hint

        @contextlib.contextmanager
        def raise_internal_exceptions():
            yield
            # returning False causes exceptions to be re-raised. this is what we want here for debugging.
            return False

        # monkeypatch Sentry SDK methods. this relies on the implementation of EventHandler.
        monkeypatch.setattr(sentry_sdk, "capture_event", record_event)
        monkeypatch.setattr(sentry_sdk.integrations.logging, "capture_internal_exceptions", raise_internal_exceptions)

        # Setup logger and handler
        self.handler = EventHandler(level=logging.ERROR)
        self.logger = logging.getLogger("test_fixture")
        self.logger.setLevel(logging.ERROR)
        self.logger.addHandler(self.handler)

    @property
    def event(self):
        assert self._fixture_event is not None, "No event was captured."
        return self._fixture_event

    @property
    def hint(self):
        assert self._fixture_hint is not None, "No hint was captured."
        return self._fixture_hint


@pytest.fixture
def error_log_event_and_hint_exc_info(monkeypatch):
    helper = EventCaptureHelper(monkeypatch)
    helper.logger.error("an error occurred", exc_info=True)
    return helper.event, helper.hint


def test_log_event_no_exc_info(monkeypatch):
    helper = EventCaptureHelper(monkeypatch)

    def error(message, *args):
        # not inlining calls to logger.error so all emitted log records have the same line number
        helper.logger.error(message, *args, exc_info=False)
        return helper.event, helper.hint

    event_1, hint_1 = error("an error occurred with user %s", "one")
    event_2, hint_2 = error("an error occurred with user %s", "two")
    event_3, hint_3 = error("an error occurred with user %s", "three")

    event_limiter = PerProcessPerIssueEventLimiter(rate_limit_number_of_events=2, rate_limit_window_minutes=1)

    now = datetime.now(tz=timezone.utc)
    with freeze_time(now):
        drop_1 = event_limiter.should_rate_limit(event_1, hint_1)
        drop_2 = event_limiter.should_rate_limit(event_2, hint_2)
        drop_3 = event_limiter.should_rate_limit(event_3, hint_3)
    assert [drop_1, drop_2, drop_3] == [False, False, True], (
        "error logs with parameters should be rate-limited as a single issue"
    )


def test_log_event_with_exc_info(error_log_event_and_hint_exc_info: tuple[dict, dict]):
    event, hint = error_log_event_and_hint_exc_info
    event_limiter = PerProcessPerIssueEventLimiter(rate_limit_number_of_events=2, rate_limit_window_minutes=1)

    now = datetime.now(tz=timezone.utc)
    with freeze_time(now):
        decisions = [event_limiter.should_rate_limit(event, hint) for _ in range(3)]

    assert decisions == [False, False, True]


def test_event_limiter_rate_limit():
    event_limiter = PerProcessPerIssueEventLimiter(rate_limit_number_of_events=3, rate_limit_window_minutes=1)

    try:
        f_raise_1()
    except ValueError as e:
        event, hint = event_from_exception(e)
    try:
        f_raise_2()
    except ValueError as e:
        event_2, hint_2 = event_from_exception(e)

    now = datetime.now(tz=timezone.utc)
    with freeze_time(now):
        decisions = [event_limiter.should_rate_limit(event, hint) for _ in range(4)]
        decisions.extend([event_limiter.should_rate_limit(event_2, hint_2) for _ in range(4)])

    # first event should be rate limited, second event should then still be allowed until it also hits the limit.
    assert decisions == [False, False, False, True, False, False, False, True]

    # rate limit should allow new events after a while
    with freeze_time(now + timedelta(minutes=1, seconds=1)):
        decisions = [event_limiter.should_rate_limit(event, hint) for _ in range(2)]
    assert decisions == [False, False]

    with freeze_time(now + timedelta(minutes=1, seconds=45)):
        decisions = [event_limiter.should_rate_limit(event, hint) for _ in range(2)]
    assert decisions == [False, True]

    # rate limited events should not count against the rate-limit.
    with freeze_time(now + timedelta(minutes=2, seconds=2)):
        decisions = [event_limiter.should_rate_limit(event, hint) for _ in range(2)]

    assert decisions == [False, False]


def test_build_event_fingerprint():
    try:
        f_raise_1()
    except ValueError as e:
        fp_1 = build_event_fingerprint(*event_from_exception(e))

    try:
        f_raise_2()
    except ValueError as e:
        fp_2 = build_event_fingerprint(*event_from_exception(e))

    try:
        f_raise_2()
    except ValueError as e:
        fp_2_bis = build_event_fingerprint(*event_from_exception(e))

    def transform(fp):
        # the stacktraces will have different lines for the frame with the test function, we want to ignore that in
        # comparing them.
        return re.sub(f"{__file__}:([0-9]+)", f"{__file__}:<ignored>", fp, count=1)

    assert transform(fp_1) != transform(fp_2)
    assert transform(fp_2) == transform(fp_2_bis)


def test_before_send(error_log_event_and_hint_exc_info):
    # very basic "smoke test"
    class PassThroughEventLimiter(PerProcessPerIssueEventLimiter):
        def should_rate_limit(self, *args, **kwargs):
            return False

    class DropAllEventLimiter(PerProcessPerIssueEventLimiter):
        def should_rate_limit(self, *args, **kwargs):
            return True

    event_limiter = PassThroughEventLimiter()
    event, hint = error_log_event_and_hint_exc_info
    assert event_limiter.before_send(event, hint) == event

    event_limiter = DropAllEventLimiter()
    event, hint = error_log_event_and_hint_exc_info
    assert event_limiter.before_send(event, hint) is None


def test_unknown_event_not_dropped(caplog):
    event_limiter = PerProcessPerIssueEventLimiter(rate_limit_number_of_events=2, rate_limit_window_minutes=1)
    event = event_limiter.before_send({}, {})
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1, (
        "should log a warning when encountering an unexpected event format"
    )
    assert event is not None, "event with unexpected format should not be dropped"
