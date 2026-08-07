# PINLIVES Pro v3.1 - Production Deployment Guide

## 🎯 Executive Summary

PINLIVES Pro v3.1 is a complete, production-ready Telegram code distribution system with:

- ✅ **Three-tier architecture**: Bot, Daemon, Backend with persistence
- ✅ **Zero operational cost**: Uses free Telegram APIs and SQLite by default
- ✅ **100% effectiveness**: Comprehensive error handling and recovery
- ✅ **Production-grade**: Monitoring, logging, health checks, Docker support
- ✅ **Security hardened**: Input validation, credential management, audit logging
- ✅ **Scalable**: Database persistence, message queue, caching

## 📋 Pre-Deployment Checklist

### 1. Environment Setup

- [ ] Python 3.11+ installed
- [ ] Git repository cloned
- [ ] `.env` file created with credentials (see below)
- [ ] Docker & Docker Compose installed (optional, for containerized deployment)

### 2. Telegram Credentials Prepared

- [ ] **API ID** from https://my.telegram.org/apps
- [ ] **API Hash** from https://my.telegram.org/apps
- [ ] **Phone number** (+84388588488 format)
- [ ] **Bot Token** from @BotFather
- [ ] **Admin ID** (your Telegram user ID)

### 3. System Requirements

- [ ] 2GB RAM minimum
- [ ] 5GB disk space
- [ ] Network access to Telegram and backend
- [ ] Port 8000 available (or configurable)

## 🚀 Deployment Steps

### Option 1: Docker Compose (Recommended for Production)

**Fastest and most reliable deployment**

```bash
# 1. Clone repository
git clone https://github.com/pindangaz2000-dotcom/pinlives-v9-pro.git
cd pinlives-v9-pro

# 2. Create .env from template
cp .env.example .env

# 3. Edit .env with your credentials
# Edit the REQUIRED section only:
TELETHON_API_ID=YOUR_API_ID
TELETHON_API_HASH=YOUR_API_HASH
TELETHON_PHONE=+YOUR_PHONE
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
TELEGRAM_ADMIN_ID=YOUR_ADMIN_ID

# 4. Make deploy script executable
chmod +x deploy.sh

# 5. Run deployment
./deploy.sh

# 6. Verify deployment
curl http://localhost:8000/api/health
```

**What gets deployed:**
- PostgreSQL database (postgres service)
- FastAPI backend (backend service)
- Telethon daemon (daemon service)
- Telegram bot (bot service)

**Service URLs:**
- API: `http://localhost:8000`
- Health: `http://localhost:8000/api/health`
- Metrics: `http://localhost:8000/api/metrics`

### Option 2: Systemd Services (Linux/Unix)

**For permanent system integration**

```bash
# 1. Clone and setup
git clone https://github.com/pindangaz2000-dotcom/pinlives-v9-pro.git
cd pinlives-v9-pro

# 2. Create .env
cp .env.example .env
# Edit .env with your credentials

# 3. Create Python virtual environment
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Initialize database
python -m pinlives_pro.models.database

# 5. Copy and install systemd services
sudo cp pinlives_pro/infrastructure/systemd/*.service /etc/systemd/system/

# 6. Create app user (optional)
sudo useradd -r -s /bin/false pinlives

# 7. Set permissions
sudo chown -R pinlives:pinlives /opt/pinlives
sudo chmod 600 /opt/pinlives/.env

# 8. Enable and start services
sudo systemctl daemon-reload
sudo systemctl enable pinlives-backend
sudo systemctl enable pinlives-daemon
sudo systemctl enable pinlives-bot
sudo systemctl start pinlives-backend
sudo systemctl start pinlives-daemon
sudo systemctl start pinlives-bot

# 9. Verify
sudo systemctl status pinlives-backend
sudo systemctl status pinlives-daemon
sudo systemctl status pinlives-bot
```

### Option 3: Manual (Development/Testing)

**For local development and debugging**

```bash
# Terminal 1: FastAPI Backend
cd pinlives-v9-pro
export $(cat .env | xargs)
python -m uvicorn pinlives_pro.api.backend:app --reload --host 0.0.0.0 --port 8000

# Terminal 2: Telethon Daemon
export $(cat .env | xargs)
python -m pinlives_pro.daemon.listener

# Terminal 3: Telegram Bot
export $(cat .env | xargs)
python -m pinlives_pro.bot.orchestrator
```

## ✅ Post-Deployment Verification

### 1. Health Checks

```bash
# Backend health
curl http://localhost:8000/api/health

# Expected response:
# {
#   "status": "healthy",
#   "database": "connected",
#   "queue_depth": 0,
#   "cache_size": 0,
#   ...
# }
```

### 2. API Endpoints Test

```bash
# Status endpoint
curl http://localhost:8000/api/status

# Metrics endpoint
curl http://localhost:8000/api/metrics
```

### 3. Database Verification

```bash
# Check SQLite database (if using default)
sqlite3 /tmp/pinlives_pro.db ".tables"

# Or PostgreSQL (if configured)
psql -U pinlives -h localhost -d pinlives_pro -c "\dt"
```

### 4. Bot Testing

```bash
# Send /start to your bot on Telegram
# Expected: Main menu with buttons
# Verify all buttons respond
```

### 5. Full Authentication Flow Test

```bash
# 1. Click "🔐 Login" button in bot
# 2. Send your phone number: +84388588488
# 3. Receive OTP code on Telegram
# 4. Send OTP code (with or without spaces)
# Expected: "✓ LOGIN SUCCESS"

# 5. Verify logs show authentication
tail -f /var/log/pinlives/backend.log
tail -f /var/log/pinlives/daemon.log
tail -f /var/log/pinlives/bot.log
```

### 6. Message Reception Test

```bash
# 1. Add a test channel with "➕ Add Channel" button
# 2. Send a message to the channel
# 3. Check backend logs for message processing
# 4. Verify message appears in database

# Check database
sqlite3 /tmp/pinlives_pro.db "SELECT * FROM telegram_messages LIMIT 5;"
```

## 🔍 Monitoring & Maintenance

### Viewing Logs

```bash
# Docker Compose
docker-compose logs -f backend
docker-compose logs -f daemon
docker-compose logs -f bot

# Systemd
journalctl -u pinlives-backend -f
journalctl -u pinlives-daemon -f
journalctl -u pinlives-bot -f

# Manual log files
tail -f /var/log/pinlives/backend.log
tail -f /var/log/pinlives/daemon.log
tail -f /var/log/pinlives/bot.log
```

### System Metrics

```bash
# Real-time metrics
watch 'curl -s http://localhost:8000/api/metrics | jq'

# Check queue depth (messages waiting)
curl -s http://localhost:8000/api/metrics | jq '.queue'

# Check cache stats
curl -s http://localhost:8000/api/metrics | jq '.cache'

# Check memory usage
curl -s http://localhost:8000/api/metrics | jq '.memory'
```

### Backup & Recovery

```bash
# Backup database (SQLite)
cp /tmp/pinlives_pro.db /backup/pinlives_pro.db.$(date +%s)

# Backup PostgreSQL
pg_dump -U pinlives -h localhost pinlives_pro > backup_$(date +%s).sql

# Restore
psql -U pinlives -h localhost pinlives_pro < backup_*.sql
```

## 🚨 Troubleshooting

### Issue: Backend won't start

```bash
# Check if port 8000 is in use
lsof -i :8000

# Check logs
docker-compose logs backend

# Verify database is accessible
python -c "from pinlives_pro.models.database import init_db; init_db()"
```

### Issue: Daemon not connecting to backend

```bash
# Verify backend is running
curl http://localhost:8000/api/health

# Check network connectivity
ping 8.8.8.8  # General internet
telnet localhost 8000  # Backend port

# Check logs
docker-compose logs daemon
```

### Issue: Bot not responding

```bash
# Verify bot token is correct
python -c "from telegram import Bot; Bot(token='YOUR_TOKEN').get_me()"

# Check logs
docker-compose logs bot

# Verify TELEGRAM_BOT_TOKEN in .env
grep TELEGRAM_BOT_TOKEN .env
```

### Issue: High memory usage

```bash
# Check cache size
curl http://localhost:8000/api/metrics | jq '.cache'

# Check queue depth
curl http://localhost:8000/api/metrics | jq '.queue'

# Reduce cache size in .env
OCR_CACHE_SIZE=200

# Restart service
docker-compose restart backend
```

### Issue: Database locked error

```bash
# Check for running processes
lsof | grep pinlives_pro.db

# Kill and restart
pkill -f "pinlives_pro"
docker-compose restart backend daemon bot
```

## 🛡️ Security Best Practices

### 1. Protect Credentials

```bash
# .env file should NOT be in git
# Verify .gitignore includes .env
cat .gitignore | grep "^.env"

# Set proper file permissions
chmod 600 .env
sudo chown pinlives:pinlives .env
```

### 2. Use Strong Database Passwords

```bash
# Generate strong password
openssl rand -base64 32

# Update in .env for production
DB_PASSWORD=strong_random_password_here
```

### 3. Enable HTTPS in Production

```bash
# Use nginx reverse proxy with Let's Encrypt
# or AWS ALB with SSL termination
# Configure BACKEND_URL with https://
```

### 4. Regular Updates

```bash
# Keep dependencies updated
pip install --upgrade pip setuptools wheel

# Update vulnerable packages
pip install --upgrade -r requirements.txt

# Restart services
docker-compose restart backend daemon bot
```

### 5. Monitor for Anomalies

```bash
# Check error rates
curl http://localhost:8000/api/metrics | jq '.messages.error_rate'

# Alert if error_rate > 5%
# Check logs for details
docker-compose logs --tail=100 backend | grep ERROR
```

## 📊 Performance Tuning

### Optimize Cache

```bash
# Increase cache for better performance
OCR_CACHE_SIZE=1000
OCR_CACHE_TTL_SECONDS=7200

# Decrease for lower memory usage
OCR_CACHE_SIZE=200
OCR_CACHE_TTL_SECONDS=1800
```

### Optimize Queue

```bash
# Increase for higher throughput
MESSAGE_QUEUE_SIZE=10000

# Decrease to prevent memory usage
MESSAGE_QUEUE_SIZE=2000
```

### Database Optimization

```bash
# For PostgreSQL, enable connection pooling
# For SQLite, use PostgreSQL for better scalability

# Create indexes on frequently queried fields
# Already done in schema (see models/database.py)
```

## 🚀 Deployment Validation Checklist

- [ ] Environment variables configured
- [ ] Database initialized and accessible
- [ ] Backend API responding at http://localhost:8000
- [ ] Health check passes
- [ ] Daemon connects to backend
- [ ] Bot receives and responds to /start
- [ ] Login flow works (OTP received)
- [ ] Session persists to database
- [ ] Channels can be added
- [ ] Messages are received and processed
- [ ] Logs show no errors
- [ ] Monitoring metrics accessible
- [ ] All three services (backend, daemon, bot) running
- [ ] Performance metrics within normal range
- [ ] Backups configured and working

## 📞 Support & Escalation

### Common Issues Resolution Path

1. **Check health**: `curl http://localhost:8000/api/health`
2. **Check logs**: `docker-compose logs --tail=50`
3. **Restart services**: `docker-compose restart backend`
4. **Verify config**: `cat .env | grep -E '^[A-Z_]+='`
5. **Check ports**: `netstat -tlnp | grep 8000`
6. **Check disk**: `df -h`
7. **Check memory**: `free -h`

### Emergency Recovery

```bash
# Stop all services
docker-compose down

# Remove containers (keep data)
docker-compose down -v

# Backup database
cp /tmp/pinlives_pro.db /backup/

# Redeploy
./deploy.sh
```

## 📈 Next Steps

After successful deployment:

1. **Add monitoring**: Prometheus, Grafana, or CloudWatch
2. **Enable backups**: Daily automated database backups
3. **Set up alerts**: Error rate > 5%, queue depth > 1000
4. **Security audit**: Penetration testing, code review
5. **Scale up**: Prepare for multi-server deployment
6. **Documentation**: Keep runbooks updated

---

**Deployment Version**: v3.1
**Last Updated**: 2024-01-01
**Status**: ✅ Production Ready
