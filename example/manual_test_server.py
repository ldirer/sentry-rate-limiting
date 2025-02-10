import argparse
import logging
import random
import socket

import sentry_config
import sentry_sdk
from sentry_sdk.utils import event_from_exception

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def error_one():
    raise ValueError("one")


def error_two():
    raise ValueError("two")


def log_with_exc_info():
    logger.error("[with exc_info] something went wrong with %s", random.choice("abcde"), exc_info=True)


def log_without_exc_info():
    logger.error("[without exc_info] something went wrong with %s", random.choice("abcde"))


def handle(command: str):
    if command == "1":
        error_one()
    elif command == "2":
        error_two()
    elif command == "3":
        log_with_exc_info()
    elif command == "4":
        log_without_exc_info()
    else:
        print("unknown command received:", command)


def help(host, port):
    return f"""listening on {host}:{port}

    The sentry server should receive at most {sentry_config.settings.SENTRY_RATE_LIMIT_PER_ISSUE_NUMBER_OF_EVENTS}
    events per issue per window of {sentry_config.settings.SENTRY_RATE_LIMIT_PER_ISSUE_WINDOW_MINUTES} minutes.

    send commands with (e.g.): nc -u {host} {port}
    a command raises a given exception or logs an error, processed by Sentry
    accepted commands: 1, 2, 3, 4"""


def listen(host: str, port: int):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind((host, port))
        print(help(host, port))
        while True:
            data, _ = s.recvfrom(1024)
            command = data.decode("utf-8").strip()
            try:
                handle(command)
            except Exception as e:
                _capture_exception(e)
                print(f"Raised error: {e}")


def _capture_exception(exc: Exception):
    """copied from https://github.com/getsentry/sentry-python/blob/master/sentry_sdk/integrations/asgi.py"""
    client = sentry_sdk.get_client()
    event, hint = event_from_exception(
        exc,
        client_options=client.options,
        mechanism={"handled": False},
    )
    sentry_sdk.capture_event(event, hint=hint)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Simple UDP server to test Sentry configuration. "
        "Listens for commands to raise exceptions/log errors."
    )
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host address to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port number to listen on")
    args = parser.parse_args()
    listen(args.host, args.port)
