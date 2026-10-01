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

# uv, to install remote-code-bridge on the host the way users do.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ARG RCB_UID=1000
# ubuntu:24.04 ships an `ubuntu` user with UID 1000; free the UID for the test user.
RUN if id ubuntu >/dev/null 2>&1; then userdel --remove ubuntu; fi \
    && useradd --uid "$RCB_UID" --home-dir /fixture/home --no-create-home --shell /bin/bash rcb

COPY remote-entrypoint.sh /usr/local/bin/remote-entrypoint.sh
RUN chmod 755 /usr/local/bin/remote-entrypoint.sh
