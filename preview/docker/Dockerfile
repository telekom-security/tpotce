FROM alpine:edge
#
# Include dist
COPY dist/ /root/dist/
#
# Get and install dependencies & packages
RUN apk --no-cache -U add \
             aria2 \
             apache2-utils \ 
             bash \
	     bind-tools \
             conntrack-tools \
	     curl \
	     ethtool \
	     figlet \
             git \
             grep \
	     iproute2 \
	     iptables \ 
	     jq \
	     logrotate \
	     lsblk \
	     net-tools \
	     openssl \
	     pigz \
	     tar \
	     uuidgen && \
    apk --no-cache -U add --repository=https://dl-cdn.alpinelinux.org/alpine/edge/community \
             yq && \
#
# Setup user
    addgroup -g 2000 tpot && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 tpot && \
#
# Install tpot
    git clone --depth=1 https://github.com/telekom-security/tpotce /opt/tpot && \
    cd /opt/tpot && \
    sed -i "s#/opt/tpot/etc/logrotate/status#/data/tpot/etc/logrotate/status#g" bin/clean.sh && \
    sed -i "s#/opt/tpot/etc/compose/elk_environment#/data/tpot/etc/compose/elk_environment#g" bin/clean.sh && \
    sed -i "s#/usr/sbin/iptables-legacy#/sbin/iptables-legacy#g" bin/rules.sh && \
    sed -i "s/tr -d '\", '/tr -d '\", ,#,-'/g" bin/rules.sh && \
    sed -i "s#/opt/tpot/etc/compose/elk_environment#/data/tpot/etc/compose/elk_environment#g" bin/updateip.sh && \
    sed -i "s#.*myLOCALIP=.*#myLOCALIP=\$(/sbin/ip address show | awk '/inet .*brd/{split(\$2,a,\"/\"); print a[1]; exit}')#" bin/updateip.sh && \
    sed -i "s#.*myUUID=.*#myUUID=\$(cat /data/uuid)#" bin/updateip.sh && \
    sed -i "s#/etc/issue#/tmp/etc/issue#g" bin/updateip.sh && \
    sed -i "/toilet/d" bin/updateip.sh && \
    sed -i "/source \/etc\/environment/d" bin/updateip.sh && \
    touch /opt/tpot/etc/tpot.yml && \
    cp /root/dist/entrypoint.sh . && \
#
# Clean up
    apk del --purge git && \
    rm -rf /root/* /tmp/* && \
    rm -rf /root/.cache /opt/tpot/.git && \
    rm -rf /var/cache/apk/*
#
# Run tpotinit
WORKDIR /opt/tpot
HEALTHCHECK --retries=1000 --interval=5s CMD test -f /tmp/success || exit 1
STOPSIGNAL SIGKILL
CMD ["/opt/tpot/entrypoint.sh"]
