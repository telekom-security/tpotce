FROM alpine:3.16 AS builder
#
# Include dist
COPY dist/ /root/dist/
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
FROM alpine:3.20
RUN apk --no-cache -U upgrade
COPY --from=builder /root/dist/* /opt/endlessh/
#
STOPSIGNAL SIGINT
USER 2000:2000
WORKDIR /opt/endlessh/
CMD ./endlessh -f endlessh.conf >/var/log/endlessh/endlessh.log
