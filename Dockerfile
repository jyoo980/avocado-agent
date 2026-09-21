FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG CBMC_VERSION=6.9.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    wget \
    jq \
    et update && apt-get install -y --no-install-recommends "/tmp/${CBMC_DEB}" && \
    rm -f "/tmp/${CBMC_DEB}" && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV IS_SANDBOX=1

ENV VIRTUAL_ENV="/app/.venv"
ENV PATH="/app/.venv/bin:$PATH"

VOLUME ["/app"]

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
