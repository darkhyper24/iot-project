FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        libssl-dev \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# CoAP per-room UDP ports (100 CoAP rooms => base_port .. base_port+199 for safety).
EXPOSE 5683-5882/udp

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "simulator.main"]
