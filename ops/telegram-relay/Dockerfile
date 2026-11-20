# The Telegram relay (DEPLOY.md §14): one Python file, httpx (the version the app pins).
FROM python:3.12-slim
RUN pip install --no-cache-dir httpx==0.28.1 \
    && useradd --create-home --uid 10001 relay && mkdir /state && chown relay /state
COPY relay.py /app/relay.py
USER relay
ENV STATE_DIR=/state PYTHONUNBUFFERED=1
CMD ["python", "/app/relay.py"]
