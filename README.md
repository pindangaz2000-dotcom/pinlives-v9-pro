# PINLIVES Pro v3.1 - Production-Grade Telegram Code Distribution System

Complete, production-ready system for extracting and distributing codes from private Telegram channels.

## 🎯 Features

### Core Features
- ✅ Real Telethon library for private channel access
- ✅ Python-telegram-bot control panel with commands and buttons
- ✅ FastAPI backend with persistent storage
- ✅ OCR code extraction with entropy validation
- ✅ Message queue with automatic retry logic
- ✅ Session persistence and authentication

### Production Features
- ✅ SQLite/PostgreSQL database persistence
- ✅ Comprehensive health checks and monitoring
- ✅ Structured logging with audit trail
- ✅ Security: input validation, rate limiting, secure config
- ✅ Docker containerization for easy deployment
- ✅ System metrics (CPU, memory, disk, queue depth)
- ✅ Error handling with graceful degradation
- ✅ LRU cache with bounded memory
- ✅ Atomic database operations

## 📋 Requirements

- Python 3.11+
- Docker & Docker Compose (for containerized deployment)
- PostgreSQL (optional, defaults to SQLite)

### Telegram Setup

1. **Create Telegram Application**:
   - Go to https://my.telegram.org/apps
   - Create Application to get `API_ID` and `API_HASH`

2. **Create Telegram Bot**:
   - Chat with @BotFather
   - Create bot to get `BOT_TOKEN`
   - Get your `ADMIN_ID` from @userinfobot

## 🚀 Quick Start

### 1. Clone and Setup

```bash
git clone https://github.com/pindangaz2000-dotcom/pinlives-v9-pro.git
cd pinlives-v9-pro

# Copy environment template
cp .env.example .env

# Edit .env with your credentials
nano .env
```

### 2. Configure Environment

Edit `.env` with your Telegram credentials:

```bash
# Required: Your Telegram credentials
TELETHON_API_ID=32943718
TELETHON_API_HASH=your_hash_from_my.telegram.org
TELETHON_PHONE=+84388588488

# Required: Your bot and admin details
TELEGRAM_BOT_TOKEN=your_token_from_botfather
TELEGRAM_ADMIN_ID=your_telegram_id
```

### 3. Deploy with Docker

```bash
# Make deploy script executable
chmod +x deploy.sh

# Deploy (creates all containers)
./deploy.sh
```

### 4. Alternative: Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Initialize database
python -m pinlives_pro.models.database

# Start backend (one terminal)
python -m uvicorn pinlives_pro.api.backend:app --reload

# Start daemon (another terminal)
python -m pinlives_pro.daemon.listener

# Start bot (another terminal)
python -m pinlives_pro.bot.orchestrator
```

## 🏗️ Architecture

### Three-Component System

```
┌─────────────────────────────────────────────────────────┐
│ PINLIVES Pro v3.1 - Production System                   │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─────────────────┐    ┌──────────────────┐           │
│  │  Telegram Bot   │    │ Telethon Daemon  │           │
│  │  Control Panel  │    │  (Real Channel   │           │
│  │  (Commands)     │───▶│   Listening)     │           │
│  └────────┬────────┘    └────────┬─────────┘           │
│           │                      │                      │
│           └──────────┬───────────┘                      │
│                      ▼                                   │
│          ┌──────────────────────┐                       │
│          │  FastAPI Backend     │                       │
│          │  (Orchestration)     │                       │
│          └──────────┬───────────┘                       │
│                     ▼                                    │
│          ┌──────────────────────┐                       │
│          │ PostgreSQL/SQLite DB │                       │
│          │  (Persistence)       │                       │
│          └──────────────────────┘                       │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Bot** sends commands via `/start` (login), buttons (manage)
2. **Backend** receives login request, calls Telethon daemon
3. **Daemon** connects to Telegram with real account
4. **Daemon** receives messages from private channels
5. **Daemon** sends messages to backend API
6. **Backend** processes messages asynchronously (queue)
7. **Backend** extracts codes using OCR
8. **Database** stores all data persistently
9. **Bot** queries backend for status and logs

## 🔧 Configuration

### Environment Variables

```bash
# REQUIRED - Telegram App
TELETHON_API_ID=your_api_id
TELETHON_API_HASH=your_api_hash
TELETHON_PHONE=+1234567890

# REQUIRED - Bot
TELEGRAM_BOT_TOKEN=bot_token
TELEGRAM_ADMIN_ID=admin_id

# OPTIONAL - Backend (defaults provided)
BACKEND_URL=http://localhost:8000
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000

# OPTIONAL - Database (default: SQLite)
DB_TYPE=sqlite  # or 'postgres'
DB_PATH=/tmp/pinlives_pro.db

# OPTIONAL - Cache & Queue
OCR_CACHE_SIZE=500
MESSAGE_QUEUE_SIZE=5000

# OPTIONAL - Logging
LOG_LEVEL=INFO
ENABLE_MONITORING=true
```

## 📊 API Endpoints

### Health & Monitoring

```bash
# Health check
curl http://localhost:8000/api/health

# Status
curl http://localhost:8000/api/status

# Detailed metrics
curl http://localhost:8000/api/metrics
```

### Authentication

```bash
# Start login flow
curl -X POST http://localhost:8000/api/telethon/login/start \
  -H "Content-Type: application/json" \
  -d '{"phone": "+84388588488"}'

# Verify OTP
curl -X POST http://localhost:8000/api/telethon/login/verify \
  -H "Content-Type: application/json" \
  -d '{"phone": "+84388588488", "otp": "12345"}'
```

### Channels

```bash
# Add channel
curl -X POST http://localhost:8000/api/telethon/channel/add \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "+84388588488",
    "channel_id": -1001234567890,
    "channel_name": "Channel Name"
  }'
```

### Messages

```bash
# Receive message (from daemon)
curl -X POST http://localhost:8000/api/telethon/message \
  -H "Content-Type: application/json" \
  -d '{
    "chat_id": -1001234567890,
    "chat_name": "Channel Name",
    "message_id": 12345,
    "text": "Code: ABC123",
    "date": "2024-01-01T12:00:00Z",
    "has_media": false
  }'
```

## 🧪 Testing

### Run Tests

```bash
# All tests
pytest -v

# With coverage
pytest --cov=pinlives_pro

# Specific test file
pytest tests/test_backend.py -v
```

### Manual Testing Checklist

- [ ] 1. Backend health check responds
- [ ] 2. Database initialization successful
- [ ] 3. Login flow requests OTP
- [ ] 4. OTP verification authenticates user
- [ ] 5. Session persists to database
- [ ] 6. Add channel succeeds
- [ ] 7. Message ingestion works
- [ ] 8. Queue processes messages
- [ ] 9. Cache works with LRU eviction
- [ ] 10. Monitoring metrics available

## 🚀 Deployment Options

### Option 1: Docker Compose (Recommended)

```bash
./deploy.sh
```

Creates 4 containers:
- PostgreSQL database
- FastAPI backend
- Telethon daemon
- Telegram bot

### Option 2: Systemd Services

```bash
sudo cp pinlives_pro/infrastructure/systemd/pinlives-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start pinlives-backend pinlives-daemon pinlives-bot
sudo systemctl enable pinlives-backend pinlives-daemon pinlives-bot
```

### Option 3: Manual (Development)

```bash
# Terminal 1: Backend
python -m uvicorn pinlives_pro.api.backend:app

# Terminal 2: Daemon
python -m pinlives_pro.daemon.listener

# Terminal 3: Bot
python -m pinlives_pro.bot.orchestrator
```

## 📈 Monitoring

### System Health

```bash
# Check all components
watch 'curl -s http://localhost:8000/api/health | jq'

# View logs
docker-compose logs -f backend
docker-compose logs -f daemon
docker-compose logs -f bot
```

### Key Metrics

- **Queue Depth**: Messages awaiting processing
- **Cache Hit Rate**: OCR result reusability
- **Error Rate**: Failed operations percentage
- **Memory Usage**: Process memory consumption
- **Message Throughput**: Messages processed per minute

## 🛡️ Security

### Built-in Security

- ✅ Environment variable-based secrets (no hardcoding)
- ✅ Input validation on all API endpoints
- ✅ SQL injection protection via ORM
- ✅ Session isolation per user
- ✅ Audit logging of all operations
- ✅ Rate limiting on API endpoints
- ✅ Error handling without info disclosure

### Deployment Security

- ✅ Use strong database passwords
- ✅ Run behind reverse proxy (nginx)
- ✅ Enable HTTPS/TLS in production
- ✅ Restrict API access by IP
- ✅ Regular security updates
- ✅ Monitor logs for anomalies

## 📝 Logging

### Log Locations

- **Backend**: `/var/log/pinlives/backend.log`
- **Daemon**: `/var/log/pinlives/daemon.log`
- **Bot**: `/var/log/pinlives/bot.log`

### Log Levels

- `DEBUG`: Detailed debugging info
- `INFO`: General information
- `WARNING`: Warning conditions
- `ERROR`: Error conditions

## 🆘 Troubleshooting

### Database Connection Error

```bash
# Check database
docker-compose logs postgres

# Verify credentials in .env
# Restart database
docker-compose restart postgres
```

### Bot Not Responding

```bash
# Check bot logs
docker-compose logs bot

# Verify TELEGRAM_BOT_TOKEN
# Verify TELEGRAM_ADMIN_ID
```

### Daemon Not Connecting

```bash
# Check daemon logs
docker-compose logs daemon

# Verify TELETHON_API_ID, API_HASH, PHONE
# Check internet connection
```

### High Memory Usage

```bash
# Check metrics
curl http://localhost:8000/api/metrics | jq '.memory'

# Check cache size
# Reduce OCR_CACHE_SIZE if needed
```

## 📚 Documentation

- [API Documentation](docs/api.md)
- [Architecture Guide](docs/architecture.md)
- [Deployment Guide](docs/deployment.md)
- [Configuration Guide](docs/configuration.md)

## 🤝 Contributing

1. Create feature branch
2. Make changes
3. Run tests: `pytest`
4. Format code: `black .`
5. Lint: `flake8`
6. Submit PR

## 📄 License

MIT License - See LICENSE file

## 📞 Support

Issues? Questions? Open an issue on GitHub.

---

**Made with ❤️ by PINLIVES Team**
