FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -m appuser

WORKDIR /app

# System libraries for WeasyPrint (HTML/CSS → PDF): pango/cairo/gdk-pixbuf
# render the branded documents incl. RTL/Persian shaping. fonts-* provide a
# Latin and an Arabic/Persian fallback so PDFs render the same everywhere.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2 \
        libffi8 fonts-dejavu-core fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Pre-create the upload subdirs the app touches at import/runtime. `uploads/` is
# excluded from the build context (.dockerignore) and is mounted as a named
# volume in prod; Docker seeds an empty named volume from the image path's
# contents+ownership, so creating these as appuser makes the volume writable by
# the non-root process. Without it the volume mounts root-owned and the app
# crashes on `uploads/snapshots` at import time.
RUN mkdir -p /app/app/uploads/snapshots \
             /app/app/uploads/transactions \
             /app/app/uploads/invoice_imports \
             /app/app/uploads/branding \
    && chmod +x /app/entrypoint.sh \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# entrypoint.sh runs a ONE-SHOT pre-start (DB wait → migrate → seed) and only
# then execs the CMD web server. Migrations do NOT run in the CMD and MUST NOT
# be `alembic upgrade head` on their own — a fresh DB needs create_all first
# (the pre-start's _bootstrap_schema_and_seed does create_all → stamp/upgrade →
# seed in the correct order; see app/main.py). Keeping uvicorn as the CMD means
# a failed migration aborts in the entrypoint, loudly, before the server starts.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
