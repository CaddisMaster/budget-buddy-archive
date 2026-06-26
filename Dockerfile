FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
COPY . .
EXPOSE 5000
# --threads keeps one process (in-memory Flask-Limiter stays consistent) while
# letting a slow request run concurrently with others. --timeout 120 gives the
# v10.3 Ask tool-use loop room for several sequential model calls (the default
# 30s killed the worker mid-loop → the HTMX request hung).
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:5000", "app:app"]