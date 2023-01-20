FROM alpine:3.13 as builder
#
# Include dist
ADD dist/ /root/dist/
#
# Install packages
RUN apk -U add --no-cache \
            build-base \
	    git \
            libcap && \
#
# Install endlessh from git
    git clone https://github.com/skeeto/endlessh /opt/endlessh && \
    cd /opt/endlessh && \
    git checkout dfe44eb2c5b6fc3c48a39ed826fe0e4459cdf6ef && \
    make && \
    mv /opt/endlessh/endlessh /root/dist
#
FROM alpine:3.17
#
COPY --from=builder /root/dist/* /opt/endlessh/
#
# Install packages
RUN apk -U add --no-cache \
            libcap && \
#
# Setup user, groups and configs
    mkdir -p /var/log/endlessh && \
    addgroup -g 2000 endlessh && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 endlessh && \
    chown -R endlessh:endlessh /opt/endlessh && \
    #setcap cap_net_bind_service=+ep /usr/bin/python3.8 && \
#
# Clean up
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Set workdir and start endlessh
STOPSIGNAL SIGINT
USER endlessh:endlessh
WORKDIR /opt/endlessh/
CMD ./endlessh -f endlessh.conf >/var/log/endlessh/endlessh.log
