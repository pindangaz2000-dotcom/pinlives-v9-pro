FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pinlives_pro/ ./pinlives_pro/

RUN mkdir -p /data /var/log/pinlives /tmp/telethon_media

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# The image runs either the backend or the bot; docker-compose picks via command.
CMD ["python", "-m", "uvicorn", "pinlives_pro.api.backend:app", \
     "--host", "0.0.0.0", "--port", "8000"]
