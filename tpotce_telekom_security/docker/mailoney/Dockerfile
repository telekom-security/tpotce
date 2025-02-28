# For Mailoney to work we need to keep Alpine 3.19 for now
FROM alpine:3.19
#
# Install packages
RUN apk --no-cache -U upgrade && \
    apk --no-cache -U add \
		git \
		libcap \
		python3 && \
#
# Install mailoney from git
    git clone https://github.com/t3chn0m4g3/mailoney /opt/mailoney && \
    cd /opt/mailoney && \
    git checkout 06e54bd410e0044d26d8462321f1b2811318db0b && \
#
# Setup user, groups and configs
    addgroup -g 2000 mailoney && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 mailoney && \
    chown -R mailoney:mailoney /opt/mailoney && \
    setcap cap_net_bind_service=+ep $(readlink -f $(type -P python3)) && \
#
# Clean up
    apk del --purge git && \
    rm -rf /root/* /var/cache/apk/* /opt/mailoney/.git
#
# Set workdir and start mailoney
STOPSIGNAL SIGINT
USER mailoney:mailoney
WORKDIR /opt/mailoney/
CMD ["/usr/bin/python","mailoney.py","-i","0.0.0.0","-p","25","-t","schizo_open_relay"]