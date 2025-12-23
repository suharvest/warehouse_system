# Frontend Dockerfile - Multi-stage build
# Stage 1: Build frontend with Node.js
FROM node:20-alpine AS builder

WORKDIR /build

# Copy package files
COPY frontend/package*.json ./

# Install dependencies
RUN npm ci --silent

# Copy source files
COPY frontend/ ./

# Build frontend
RUN npm run build

# Stage 2: Production server with Python
FROM python:3.12-slim

WORKDIR /app

# Copy built files from builder stage
COPY --from=builder /build/dist ./dist
COPY --from=builder /build/server.py ./

ENV PYTHONUNBUFFERED=1

EXPOSE 2125

# Start frontend server
CMD ["python", "server.py"]
