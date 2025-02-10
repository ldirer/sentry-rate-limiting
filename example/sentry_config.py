import logging
from dataclasses import dataclass

import sentry_sdk
from sentry_sdk.types import Event
from sentry_sdk.types import Hint

from sentry_rate_limiting.process_event_limiter import PerProcessPerIssueEventLimiter

logger = logging.Logger(__name__)


@dataclass
class Settings:
    SENTRY_RATE_LIMIT_PER_ISSUE_NUMBER_OF_EVENTS: int = 5
    SENTRY_RATE_LIMIT_PER_ISSUE_WINDOW_MINUTES: int = 1
    SENTRY_DSN: str = "http://public@127.0.0.1:8001/1"


settings = Settings()

event_limiter = PerProcessPerIssueEventLimiter(
    rate_limit_window_minutes=settings.SENTRY_RATE_LIMIT_PER_ISSUE_WINDOW_MINUTES,
    rate_limit_number_of_events=settings.SENTRY_RATE_LIMIT_PER_ISSUE_NUMBER_OF_EVENTS,
)


def before_send(event: Event, hint: Hint) -> Event | None:
    return event_limiter.before_send(event, hint)


sentry_sdk.init(
    dsn=settings.SENTRY_DSN,
    before_send=before_send,
    # debug mode is required to get logs from errors in `before_send`
    debug=True,
)

logger.info("Using sentry with dsn: %s", sentry_sdk.get_client().dsn)
