# Docker Configuration

## Port Configuration

The application has been configured with the following port mapping:

- **Backend API**: `http://localhost:3001` (exposed from container port 8000)
- **Frontend**: `http://localhost:80` (production) or `http://localhost:5173` (development)

## Starting the Application

### Production Mode
```bash
./start.sh
```
This will use `docker-compose.yml` and run:
- Backend on port 3001 (production container)
- Frontend on port 80 (nginx/static files)

### Development Mode
```bash
./start.sh dev
```
This will use `docker-compose.dev.yml` and run:
- Backend on port 3001 (with hot reload)
- Frontend on port 5173 (Vite dev server)

## Stopping the Application

```bash
# Stop production
docker-compose down

# Stop development
docker-compose -f docker-compose.dev.yml down
```

## Configuration Details

### Backend
- Internal container port: 8000
- Exposed host port: 3001
- Health check: `http://localhost:8000/health` (internal)
- GPU acceleration enabled (NVIDIA CUDA)

### Frontend
- **Production**: Port 80 (static files via nginx)
- **Development**: Port 5173 (Vite dev server)
- API endpoint: `http://localhost:3001` (configured in `src/lib/api.ts`)
- WebSocket endpoint: `ws://localhost:3001/ws/tts/{book_id}`

## Environment Variables

The backend uses the following environment variables (defined in docker-compose.yml):
- `PYTHONUNBUFFERED=1`: Ensures Python output is not buffered
- `DB_PATH=/data/ebook_reader.db`: Database path
- `NVIDIA_VISIBLE_DEVICES=all`: GPU access
- `NVIDIA_DRIVER_CAPABILITIES=compute,utility`: GPU driver capabilities
- `UVICORN_WS=websockets`: WebSocket support

## Volumes

- `backend_uploads`: Persistent storage for uploaded files
- `backend_data`: Persistent storage for database

## Troubleshooting

### Backend not accessible on port 3001
```bash
# Check if containers are running
docker-compose ps

# Check backend logs
docker-compose logs backend

# Test backend health
curl http://localhost:3001/health
```

### Frontend cannot connect to backend
- Verify the API_BASE in `frontend/src/lib/api.ts` is set to `http://localhost:3001`
- Check that both containers are on the same network: `ebook-net`

### Port conflicts
If port 3001 is already in use, modify the port mapping in `docker-compose.yml`:
```yaml
ports:
  - "YOUR_PORT:8000"
```
And update `frontend/src/lib/api.ts` accordingly.
