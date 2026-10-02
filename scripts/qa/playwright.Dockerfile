# docker build -t aa-playwright:1.49.1 -f scripts/qa/playwright.Dockerfile scripts/qa
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble
RUN pip install --break-system-packages --timeout 30 --retries 5 -q playwright==1.49.1
