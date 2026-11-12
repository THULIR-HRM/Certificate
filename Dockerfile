FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app

# Install system dependencies (including LibreOffice for docx-to-pdf conversion)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-nogui \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run collectstatic during build
RUN python manage.py collectstatic --noinput

# Replace /usr/local/bin/gunicorn with wrapper to safely expand $PORT from any source
RUN chmod +x /app/gunicorn_wrapper.py && \
    cp /app/gunicorn_wrapper.py /usr/local/bin/gunicorn

EXPOSE 8000

CMD ["python", "/app/gunicorn_wrapper.py", "config.wsgi:application"]
