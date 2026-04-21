FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    curl \
    wget \
    git \
    build-essential \
    cmake \
    flex \
    bison \
    libxml2-utils \
    cbmc \
    && rm -rf /var/lib/apt/lists/*

# Install uv (manages Python installs and dependencies)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Install Python 3.13 via uv
RUN uv python install 3.13

WORKDIR /app

# Install dependencies before copying the full source for better layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .
RUN uv sync --frozen

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["uv", "run", "python", "main.py"]
