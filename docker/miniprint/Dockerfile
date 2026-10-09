# syntax=docker/dockerfile:1
# miniprint – printer honeypot (PJL on 9100, web admin on 8000)
FROM python:3.14-alpine3.24 AS build
COPY --from=ghcr.io/astral-sh/uv:0.12@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
ARG MINIPRINT_VERSION=v0.2.2
# only the server and its seed files, no tests or docs in the image
RUN apk --no-cache add git \
    && git clone --depth 1 --branch ${MINIPRINT_VERSION} https://github.com/t3chn0m4g3/miniprint /root/miniprint \
    && mkdir -p /opt/miniprint \
    && cp -r /root/miniprint/pyproject.toml /root/miniprint/uv.lock /root/miniprint/README.md \
             /root/miniprint/*.py /root/miniprint/fake-files /opt/miniprint/
WORKDIR /opt/miniprint
RUN uv sync --locked --no-dev --no-install-project

FROM python:3.14-alpine3.24
RUN apk --no-cache -U upgrade \
    && addgroup -g 2000 miniprint \
    && adduser -S -H -s /sbin/nologin -u 2000 -G miniprint miniprint
COPY --from=build /opt/miniprint /opt/miniprint
RUN mkdir -p /opt/miniprint/log /opt/miniprint/uploads /opt/miniprint/data \
    && chown miniprint:miniprint /opt/miniprint/log /opt/miniprint/uploads /opt/miniprint/data
ENV PATH=/opt/miniprint/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

EXPOSE 9100 8000

# probes the ports of the running server (read from /proc/1/cmdline) on loopback
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD MINIPRINT_HEALTHCHECK=1 python ./server.py || exit 1

STOPSIGNAL SIGINT
WORKDIR /opt/miniprint
USER miniprint:miniprint
# port 80 belongs to h0neytr4p, the web admin listens on 8000
CMD ["python", "./server.py", \
     "--bind", "0.0.0.0", \
     "--http-port", "8000", \
     "--log-file", "log/miniprint.json", \
     "--uploads-dir", "uploads", \
     "--state-dir", "data", \
     "--timeout", "60", \
     "--session-timeout", "300", \
     "--max-connections", "16", \
     "--max-request-bytes", "65536", \
     "--max-job-bytes", "1048576", \
     "--max-virtual-file-bytes", "262144", \
     "--max-response-bytes", "131072"]
