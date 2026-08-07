#!/bin/bash
# PINLIVES Pro v3.1 - Deployment Script

set -e

echo "================================================"
echo "PINLIVES Pro v3.1 - Deployment"
echo "================================================"

# Check environment
if [ ! -f .env ]; then
    echo "❌ .env file not found. Please copy .env.example to .env and configure."
    exit 1
fi

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is required. Please install Docker."
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is required. Please install Docker Compose."
    exit 1
fi

# Create directories
echo "📁 Creating directories..."
mkdir -p data/{sessions,media}
mkdir -p logs

# Build images
echo "🔨 Building Docker images..."
docker-compose build --no-cache

# Start services
echo "🚀 Starting services..."
docker-compose up -d

# Wait for services to be ready
echo "⏳ Waiting for services to be healthy..."
sleep 10

# Check health
echo "🏥 Checking system health..."
if curl -s http://localhost:8000/api/health | grep -q "healthy"; then
    echo "✓ Backend is healthy"
else
    echo "❌ Backend health check failed"
    exit 1
fi

# Display logs
echo ""
echo "📋 Recent logs:"
docker-compose logs --tail=20

echo ""
echo "================================================"
echo "✓ DEPLOYMENT SUCCESSFUL"
echo "================================================"
echo ""
echo "Services running:"
echo "  • Backend API: http://localhost:8000"
echo "  • Database: localhost:5432"
echo "  • Daemon: Running"
echo "  • Bot: Running"
echo ""
echo "Check logs:"
echo "  • Backend: tail -f logs/backend.log"
echo "  • Daemon: tail -f logs/daemon.log"
echo "  • Bot: tail -f logs/bot.log"
echo ""
echo "View health:"
echo "  • curl http://localhost:8000/api/health"
echo ""
