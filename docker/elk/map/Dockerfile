FROM alpine:3.23
#
# Install packages
# Policy (t-pot-attack-map HANDOFF-v2 D40, §15): application/tool dependencies
# are exact-pinned (requirements.txt, tools/basemap.lock); Alpine OS packages
# are intentionally refreshed within the pinned 3.23 channel at image build.
# No floating pip: Alpine 3.23's py3-pip installs the pinned wheels as-is.
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
		build-base \
		curl \
		git \
		libcap \
		py3-pip \
		python3 \
		python3-dev \
		tzdata && \
#
# Install from GitHub and setup
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://github.com/telekom-security/t-pot-attack-map -b 4.0.0 && \
    cd t-pot-attack-map && \
    mv DataServer.py DataServer_v2.py && \
#
# Fetch the pinned basemap artefact (immutable release asset, SHA-256 verified
# against tools/basemap.lock; --check turns a partial download into a build
# failure). Runs BEFORE chown so the artefact is owned by map:map.
    sh tools/fetch_basemap.sh && \
    sh tools/fetch_basemap.sh --check && \
    pip3 install --break-system-packages -r requirements.txt && \
    setcap cap_net_bind_service=+ep $(readlink -f $(type -P python3)) && \
#
# Setup user, groups and configs
    addgroup -g 2000 map && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 map && \
    chown map:map -R /opt/t-pot-attack-map && \
#
# Clean up
    apk del --purge build-base \
		curl \
		git \
		python3-dev && \
    rm -rf /root/* /var/cache/apk/* \
           /opt/t-pot-attack-map/.git
#
# Start T-Pot-Attack-Map
ENV TZ=UTC
STOPSIGNAL SIGINT
USER map:map
WORKDIR /opt/t-pot-attack-map
# AttackMapServer binds loopback by default (4.0.0 security hardening);
# inside the container it must listen on the docker network for nginx.
CMD ["/bin/sh", "-c", "case \"$MAP_COMMAND\" in AttackMapServer*) exec /usr/bin/python3 $MAP_COMMAND --host 0.0.0.0 ;; *) exec /usr/bin/python3 $MAP_COMMAND ;; esac"]
