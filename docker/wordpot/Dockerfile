FROM alpine:3.24
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
		libcap \
		py3-defusedxml \
		py3-flask \
		py3-ua-parser \
		py3-werkzeug \
		python3 && \
    apk --no-cache -U add --virtual .wordpot-build-deps \
		git \
		py3-pip && \
#
# Install wordpot from git (v3.0.1), it runs from its folder with gunicorn
    git clone https://github.com/t3chn0m4g3/wordpot /opt/wordpot && \
    cd /opt/wordpot && \
    git checkout 8b9bea94f7e782cbbbe27786d0f62b08255c6d7c && \
#
# hpfeeds is only imported when HPFEEDS_ENABLED is set, T-Pot does not use it.
# pip only adds gunicorn (< 24, gunicorn.conf.py patches its internals) and user-agents.
    sed -i '/^hpfeeds/d' requirements.txt && \
    pip3 install --break-system-packages --no-cache-dir -r requirements.txt && \
    mkdir -p /opt/wordpot/logs/payloads && \
#
# Setup user, groups and configs
    addgroup -g 2000 wordpot && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 wordpot && \
    chown -R wordpot:wordpot /opt/wordpot && \
    setcap cap_net_bind_service=+ep $(readlink -f $(type -P python3)) && \
#
# Clean up
    apk del --purge .wordpot-build-deps && \
    rm -rf /root/* /root/.cache /var/cache/apk/* \
           /opt/wordpot/.git \
           /opt/wordpot/.gitattributes \
           /opt/wordpot/.gitignore \
           /opt/wordpot/Dockerfile \
           /opt/wordpot/docker-compose.yml \
           /opt/wordpot/requirements-dev.txt \
           /opt/wordpot/tests
#
# Set workdir and start wordpot
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORDPOT_LOG_DIR=/opt/wordpot/logs \
    WORDPOT_PAYLOAD_DIR=/opt/wordpot/logs/payloads
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python3 healthcheck.py
STOPSIGNAL SIGINT
USER wordpot:wordpot
WORKDIR /opt/wordpot/
CMD ["python3", "-m", "gunicorn", "--config", "gunicorn.conf.py", "wordpot:app"]
