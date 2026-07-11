FROM python:3.11-slim

WORKDIR /app

# Copy requirements first (better Docker layer caching)
COPY backend/requirements.txt backend/requirements-gpu.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    # GPU extras are optional; container GPU use additionally needs the NVIDIA
    # Container Toolkit + an OpenCL ICD, so failure here is fine (CPU fallback).
    && (pip install --no-cache-dir -r requirements-gpu.txt || true)

# Copy keeping package structure for unified 'backend.main' boot
COPY backend/ ./backend/
COPY frontend/ ./frontend/

EXPOSE 8000

# -u: unbuffered stdout so verbose activity streams live in `docker logs`
CMD ["python", "-u", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
