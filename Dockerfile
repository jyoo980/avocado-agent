FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG CBMC_VERSION=6.7.1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

RUN ARCH="$(dpkg --print-architecture)" && \
    if [ "$ARCH" = "arm64" ]; then \
        CBMC_DEB="ubuntu-24.04-arm64-cbmc-${CBMC_VERSION}-Linux.deb"; \
    elif [ "$ARCH" = "amd64" ]; then \
        CBMC_DEB="ubuntu-24.04-cbmc-${CBMC_VERSION}-Linux.deb"; \
    else \
        echo "Unsupported architecture: $ARCH" && exit 1; \
    fi && \
    wget -q "https://github.com/diffblue/cbmc/releases/download/cbmc-${CBMC_VERSION}/${CBMC_DEB}" -O "/tmp/${CBMC_DEB}" && \
    apt-get update && apt-get install -y --no-install-recommends "/tmp/${CBMC_DEB}" && \
    rm -f "/tmp/${CBMC_DEB}" && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

VOLUME ["/app"]
