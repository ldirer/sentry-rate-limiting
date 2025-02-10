# See https://github.com/willkg/kent
FROM python:3.11-slim

WORKDIR /app/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd -r kent && useradd --no-log-init -r -g kent kent


RUN pip install -U 'pip' && \
    pip install --no-cache-dir 'kent==2.1.0'

USER kent

ENTRYPOINT ["/usr/local/bin/kent-server"]
CMD ["run"]
