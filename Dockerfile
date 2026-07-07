# Klarim — shared image for the API and the scan Worker.
# The same image runs both services; docker-compose overrides the command.
FROM python:3.12-slim

# Logs sem buffer (aparecem em tempo real no docker logs).
ENV PYTHONUNBUFFERED=1

# System libraries:
#  - WeasyPrint needs pango/cairo/gdk-pixbuf for PDF rendering.
#  - libpq is needed by psycopg2 at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq5 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        libcairo2 \
        shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default command runs the API; the worker service overrides it in compose.
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
