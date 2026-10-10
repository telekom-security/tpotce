FROM alpine:3.24 AS builder
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk -U add --no-cache \
		build-base \
		git && \
#
# Install endlessh from git (T-Pot fork of skeeto/endlessh, release 1.2: Jitter, BannerFile, EMFILE)
    git clone https://github.com/t3chn0m4g3/endlessh /opt/endlessh && \
    cd /opt/endlessh && \
    git checkout ecc3dd39a7bd2ba7285f998eb8d9a38014022f75 && \
    make && \
    mv /opt/endlessh/endlessh /root/dist
#
FROM alpine:3.24
RUN apk --no-cache -U upgrade
COPY --from=builder /root/dist/* /opt/endlessh/
#
# endlessh runs as PID 1 and only handles SIGTERM (clean CLOSE and TOTALS lines)
STOPSIGNAL SIGTERM
USER 2000:2000
WORKDIR /opt/endlessh/
CMD ["/opt/endlessh/run.sh"]
