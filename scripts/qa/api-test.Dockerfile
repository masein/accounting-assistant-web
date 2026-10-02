# The app image with its test requirements (also what scratch pytest runs use):
#   docker compose build api
#   docker build -t aa-api-test --build-arg BASE=accounting-assistant-api -f scripts/qa/api-test.Dockerfile .
ARG BASE=accounting-assistant-api
FROM ${BASE}
USER root
COPY requirements.txt requirements-dev.txt /tmp/req/
RUN pip install -q --retries 10 --timeout 60 -r /tmp/req/requirements.txt -r /tmp/req/requirements-dev.txt
COPY requirements-dev.lock /tmp/req/requirements-dev.lock
RUN pip install -q --retries 10 --timeout 60 --require-hashes -r /tmp/req/requirements-dev.lock
USER appuser
