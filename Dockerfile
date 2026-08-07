FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY pinlives_pro/ ./pinlives_pro/
COPY .env.example .env

# Create necessary directories
RUN mkdir -p /tmp/telethon_sessions /tmp/telethon_media /var/log/pinlives

# Expose ports
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/api/health')" || exit 1

# Run application
CMD ["python", "-m", "uvicorn", "pinlives_pro.api.backend:app", \
     "--host", "0.0.0.0", "--port", "8000"]
