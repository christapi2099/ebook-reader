#!/bin/bash

echo "🔍 Verifying Docker Configuration..."
echo ""

# Check if docker-compose files exist
echo "📁 Checking Docker Compose files..."
if [ -f "docker-compose.yml" ]; then
    echo "✅ docker-compose.yml found"
    echo "   Backend port mapping: $(grep -A2 'backend:' docker-compose.yml | grep 'ports:' -A1 | tail -1)"
else
    echo "❌ docker-compose.yml not found"
fi

if [ -f "docker-compose.dev.yml" ]; then
    echo "✅ docker-compose.dev.yml found"
    echo "   Backend port mapping: $(grep -A2 'backend:' docker-compose.dev.yml | grep 'ports:' -A1 | tail -1)"
else
    echo "❌ docker-compose.dev.yml not found"
fi

echo ""
echo "🔧 Checking Frontend Configuration..."

if [ -f "frontend/src/lib/api.ts" ]; then
    echo "✅ frontend/src/lib/api.ts found"
    echo "   API_BASE: $(grep 'export const API_BASE' frontend/src/lib/api.ts)"
    echo "   WebSocket URL: $(grep 'ws://localhost' frontend/src/lib/api.ts)"
else
    echo "❌ frontend/src/lib/api.ts not found"
fi

echo ""
echo "🐳 Docker Status..."
docker-compose ps 2>/dev/null || echo "Docker Compose not running or not available"

echo ""
echo "✨ Configuration Summary:"
echo "   - Backend API: http://localhost:3001"
echo "   - Frontend Dev: http://localhost:5173"
echo "   - Frontend Prod: http://localhost:80"
echo ""
echo "🚀 Ready to start! Use './start.sh' for production or './start.sh dev' for development."
