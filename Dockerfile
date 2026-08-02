FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV USE_VERTEX_AI=true

# Set working directory
WORKDIR /app

# Install system requirements (sqlite3 for database storage)
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy python dependency list
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all application code
COPY . .

# Create database and static files directory inside image (if not already existing)
RUN mkdir -p /app/db /app/static

# Expose the API port
EXPOSE 8000

# Run uvicorn server
CMD ["python", "app.py"]
