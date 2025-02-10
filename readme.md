Client-side rate limiting per Sentry issue.

Events that exceed the configured rate will not be sent to Sentry.
Errors are grouped into issues by computing a basic fingerprint of the event in `before_send`. That might not match exactly the grouping computed on Sentry's servers, adjust if needed.

## Usage

See [example/sentry_config.py](./example/sentry_config.py).

Events are counted in memory. This means the rate-limiting is applied **per process**.
If you are running a web application with 4 processes, expect at most 4 times the configured max number of events.

A natural solution would be to share data between processes with something like Redis.
But it feels dangerous to introduce possible network failures here. In a thread-based web app, `before_send` runs in the request-processing thread so hanging on network access will keep that thread busy, preventing it from serving new requests.

## Development - Testing Sentry `before_send`

We can spin up a 'fake' Sentry server to help integration-test configuration callbacks like `before_send`.
Copying instructions from [kent](https://github.com/mozilla-services/kent):

    docker build -t sentry_fake -f sentry_fake.dockerfile ./

Launch with:

    docker run --init --rm -p 8001:8001 --name sentry_fake sentry_fake run --host 0.0.0.0 --port 8001

Make the Sentry DSN point to `"http://public@localhost:8001/1"`.
Errors should now be sent to the 'fake server' and appear on the dashboard at http://localhost:8001.
