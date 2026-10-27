FROM nvidia/cuda:13.4.3-devel-ubuntu26.04

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-venv \
    ffmpeg \
    x11-apps \
    libxext6 \
    libxrender-dev \
    libxrandr2 \
    x11-utils \
    mesa-utils \
    libglvnd0 \
    libglvnd-dev \
    libgl1 \
    libicu78 \
    wget \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies in an isolated environment
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv ${VIRTUAL_ENV}
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

WORKDIR /app

ARG OSU_VIEWER_VERSION=latest
ARG OSU_VIEWER_BUILD=Release
ARG OSU_VIEWER_ARCH=x64

ARG OSU_VIEWER_PACKAGE=app-${OSU_VIEWER_BUILD}-linux-${OSU_VIEWER_ARCH}.tar.gz
# Compute correct release path: "latest" or "tag/<version>"
RUN if [ "${OSU_VIEWER_VERSION}" = "latest" ]; then \
      OSU_VIEWER_RELEASE_URL="https://github.com/Airkek/osu-replay-viewer-continued/releases/latest/download/${OSU_VIEWER_PACKAGE}"; \
    else \
      OSU_VIEWER_RELEASE_URL="https://github.com/Airkek/osu-replay-viewer-continued/releases/download/${OSU_VIEWER_VERSION}/${OSU_VIEWER_PACKAGE}"; \
    fi && \
    echo "Downloading from: ${OSU_VIEWER_RELEASE_URL}" && \
    mkdir -p /app/osu-replay-viewer && \
    wget -qL "${OSU_VIEWER_RELEASE_URL}" -O /tmp/osu-replay-viewer.tar.gz && \
    tar -xzf /tmp/osu-replay-viewer.tar.gz -C /app/osu-replay-viewer && \
    rm /tmp/osu-replay-viewer.tar.gz && \
    chmod +x /app/osu-replay-viewer/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

# Copy application files (include subdirectories like src/providers)
COPY src/ /app/

# Default command
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9732", "--loop", "asyncio"]
