FROM alpine:3.24
ARG COWRIE_VERSION=v3.1.1
ARG COWRIE_COMMIT=c17c9b73d6af0972334ea1e90b20d974cb24eeca
ENV COWRIE_COMMIT=${COWRIE_COMMIT}
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
		bash \
		build-base \
		git \
		libcap \
		libffi-dev \
		openssl \
		openssl-dev \
		py3-pip \
		python3 \
		python3-dev && \
#
# Setup user
    addgroup -g 2000 cowrie && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 cowrie && \
#
# Install cowrie
    mkdir -p /home/cowrie && \
    cd /home/cowrie && \
    git clone --depth 1 --branch "${COWRIE_VERSION}" https://github.com/cowrie/cowrie && \
    cd cowrie && \
    test "$(git rev-parse HEAD)" = "${COWRIE_COMMIT}" && \
    printf "%s\n" "${COWRIE_COMMIT}" > .cowrie-source-commit && \
    mkdir -p log && \
    pip3 install --break-system-packages --upgrade --no-cache-dir pip && \
    pip3 install --break-system-packages --no-cache-dir -r requirements.txt && \
    pip3 install --break-system-packages --no-cache-dir -e . && \
    python3 /root/dist/patch_cowrie_persona_support.py --cowrie-root /home/cowrie/cowrie && \
#
# Personas: library, SSH algorithms of this Twisted, build check of every persona
    mkdir -p /usr/local/lib/tpot-cowrie && \
    cp -r /root/dist/tpot_personas /usr/local/lib/tpot-cowrie/ && \
    find /usr/local/lib/tpot-cowrie -name __pycache__ -prune -exec rm -rf {} + && \
    python3 -c "import sys; sys.path.insert(0, '/usr/local/lib/tpot-cowrie'); \
from twisted.conch.ssh.transport import SSHTransportBase as T; \
from tpot_personas import ssh_profiles as p; \
assert p.TWISTED_CIPHERS <= {c.decode() for c in T.supportedCiphers}, 'ciphers'; \
assert p.TWISTED_MACS <= {m.decode() for m in T.supportedMACs}, 'macs'" && \
    python3 /root/dist/generate_cowrie_fs.py --cowrie-root /home/cowrie/cowrie && \
    install -m 0755 /root/dist/start-cowrie-persona.py /usr/local/bin/start-cowrie-persona && \
#
# Setup configs
    setcap cap_net_bind_service=+ep $(readlink -f $(type -P python3)) && \
    chown cowrie:cowrie -R /home/cowrie/* /usr/lib/$(readlink -f $(type -P python3) | cut -f4 -d"/")/site-packages/twisted/plugins && \
#
# Start Cowrie once to prevent dropin.cache errors upon container start caused by read-only filesystem
#   (timeout ends twistd with SIGTERM: busybox passes on its 0, 124 / 143 elsewhere; any other status fails the build)
    { su - cowrie -c "export PYTHONPATH=/home/cowrie/cowrie:/home/cowrie/cowrie/src && \
                      COWRIE_PERSONA=debian-bookworm timeout 20 /usr/local/bin/start-cowrie-persona" ; \
      myRC=$? ; test "$myRC" -eq 124 || test "$myRC" -eq 143 || test "$myRC" -eq 0 ; } && \
    test -f /usr/lib/$(readlink -f $(type -P python3) | cut -f4 -d"/")/site-packages/twisted/plugins/dropin.cache && \
    rm -rf /home/cowrie/cowrie/etc /tmp/cowrie && \
#
# Clean up
    apk del --purge build-base \
                    git \
                    libcap \
                    libffi-dev \
                    openssl-dev \
                    python3-dev && \
    rm -rf /root/* /tmp/* \
           /var/cache/apk/* \
           /home/cowrie/cowrie/.git
#
# Start cowrie
ENV PYTHONPATH=/home/cowrie/cowrie:/home/cowrie/cowrie/src
WORKDIR /home/cowrie/cowrie
USER cowrie:cowrie
CMD ["/usr/local/bin/start-cowrie-persona"]
