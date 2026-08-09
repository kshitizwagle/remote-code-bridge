FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        openssh-client \
        openssh-server \
        python3 \
    && rm -rf /var/lib/apt/lists/*

ARG RCB_UID=1000
RUN useradd --uid "$RCB_UID" --home-dir /fixture/home --no-create-home --shell /bin/bash rcb

COPY remote-entrypoint.sh /usr/local/bin/remote-entrypoint.sh
RUN chmod 755 /usr/local/bin/remote-entrypoint.sh
