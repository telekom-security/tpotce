FROM alpine:3.24
#
# Add T-Pot config and entrypoint
COPY dist/mailoney.toml /root/dist/mailoney.toml
COPY --chmod=0755 dist/run.sh /usr/bin/mailoney-run.sh
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
		libcap \
		py3-cryptography \
		py3-pydantic \
		python3 && \
    apk --no-cache -U add --virtual .mailoney-build-deps \
		git && \
#
# Install mailoney from git (v3.0.0), it runs from its folder without pip
    git clone https://github.com/t3chn0m4g3/mailoney /opt/mailoney && \
    cd /opt/mailoney && \
    git checkout 52ff7a34b9772a4249aee23686163ed2e4c48e42 && \
    cp /root/dist/mailoney.toml /opt/mailoney/mailoney.toml && \
#
# Setup user, groups and configs
    addgroup -g 2000 mailoney && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 mailoney && \
    chown -R mailoney:mailoney /opt/mailoney && \
    setcap cap_net_bind_service=+ep $(readlink -f $(type -P python3)) && \
#
# Clean up
    apk del --purge .mailoney-build-deps && \
    rm -rf /root/* /var/cache/apk/* \
           /opt/mailoney/.devcontainer \
           /opt/mailoney/.env.example \
           /opt/mailoney/.git \
           /opt/mailoney/.gitignore \
           /opt/mailoney/Dockerfile \
           /opt/mailoney/docker-compose.yml \
           /opt/mailoney/smoke_test.py \
           /opt/mailoney/tests
#
# Set workdir and start mailoney
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
STOPSIGNAL SIGINT
USER mailoney:mailoney
WORKDIR /opt/mailoney/
ENTRYPOINT ["/usr/bin/mailoney-run.sh"]
