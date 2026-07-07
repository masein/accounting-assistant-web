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
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# The app's lifespan runs create_all → seed → alembic upgrade in the correct
# order (see app/main.py), so we must NOT run `alembic upgrade head` here — on a
# fresh DB that would run migrations before the base tables/seed exist.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
