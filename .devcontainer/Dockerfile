FROM python:3.12-slim

# System tools + Node.js 22
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    sudo \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Isolated Python venv — lives inside the container, not in the mounted workspace
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Make the workspace the default working directory
WORKDIR /workspace
