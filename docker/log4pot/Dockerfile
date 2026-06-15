# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM alpine:3.23 AS log4pot-src
#
# Fetch log4pot source on the build platform to keep multiarch builds light.
RUN apk --no-cache -U add \
		ca-certificates \
		git && \
    mkdir -p /opt/Log4Pot && \
    cd /opt/Log4Pot && \
    git -c init.defaultBranch=master init --quiet && \
    git remote add origin https://github.com/thomaspatzke/Log4Pot && \
    git fetch --quiet --depth 1 origin 5002b1fe0f82359ef32dbc3a899e8a701dc3256e && \
    git -c advice.detachedHead=false checkout --quiet FETCH_HEAD && \
    sed -i 's#"type": logtype,#"reason": logtype,#g' log4pot-server.py && \
    rm -rf .git
#
FROM --platform=$BUILDPLATFORM alpine:3.23 AS python-deps
ARG TARGETARCH
#
# Resolve wheel-only Python dependencies on the build platform for the target platform.
RUN apk --no-cache -U add py3-uv && \
    case "${TARGETARCH}" in \
		amd64) UV_PLATFORM="x86_64-unknown-linux-musl" ;; \
		arm64) UV_PLATFORM="aarch64-unknown-linux-musl" ;; \
		*) echo "Unsupported target architecture: ${TARGETARCH}" >&2; exit 1 ;; \
	esac && \
    uv pip install \
		--target /opt/python-deps \
		--python-version 3.12 \
		--python-platform "${UV_PLATFORM}" \
		--no-cache \
		--only-binary :all: \
		azure-storage-blob==12.30.0
#
FROM alpine:3.23
ENV PYTHONPATH=/opt/python-deps
#
# Install runtime packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
		ca-certificates \
		libcap \
		py3-boto3 \
		py3-curl \
		py3-pandas \
		python3 && \
#
# Setup user, groups, capabilities and writable paths
    mkdir -p /var/log/log4pot/log /var/log/log4pot/payloads && \
    addgroup -g 2000 log4pot && \
    adduser -S -H -s /bin/ash -u 2000 -D -G log4pot log4pot && \
    chown -R log4pot:log4pot /var/log/log4pot && \
    setcap cap_net_bind_service=+ep $(readlink -f /usr/bin/python3) && \
#
# Clean up
    rm -rf /root/* /var/cache/apk/*
#
COPY --from=python-deps /opt/python-deps /opt/python-deps
COPY --from=log4pot-src --chown=2000:2000 /opt/Log4Pot /opt/Log4Pot
#
# Start log4pot
STOPSIGNAL SIGINT
USER log4pot:log4pot
WORKDIR /opt/Log4Pot/
CMD ["/usr/bin/python3","log4pot-server.py","--port","8080","--log","/var/log/log4pot/log/log4pot.log","--payloader","--download-dir","/var/log/log4pot/payloads/","--download-timeout","15","--response","/opt/Log4Pot/responses/sap-netweaver.html"]
