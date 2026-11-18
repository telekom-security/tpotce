# Fluent Bit of a SENSOR: ships the raw log lines of the honeypots to the HIVE (dist/fluent-bit.yaml).
# The official image is distroless (no shell, no curl, root); this one is Debian with the signed package.
# A newer Fluent Bit: change FLB_VERSION, the key stays.

# The repository key, checked against its pinned fingerprint (gnupg stays in this stage)
FROM debian:trixie-slim AS key
ARG FLB_KEY_FPR=C3C0A28534B9293EAF51FABD9F9DDC083888C1CD
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg && \
    curl -fsSL https://packages.fluentbit.io/fluentbit.key -o /fluentbit.key && \
    gpg --show-keys --with-colons /fluentbit.key > /fluentbit.keyinfo && \
    test "$(grep -c '^pub:' /fluentbit.keyinfo)" = 1 && \
    myFPR="$(awk -F: '$1 == "fpr" { print $10; exit }' /fluentbit.keyinfo)" && \
    if [ "$myFPR" != "$FLB_KEY_FPR" ]; then \
      echo "The key of packages.fluentbit.io has the fingerprint $myFPR, expected $FLB_KEY_FPR." >&2; exit 1; \
    fi && \
    gpg --dearmor < /fluentbit.key > /fluentbit.gpg

FROM debian:trixie-slim
ARG FLB_VERSION=5.2.0
COPY --from=key /fluentbit.gpg /usr/share/keyrings/fluentbit.gpg
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl && \
    echo "deb [signed-by=/usr/share/keyrings/fluentbit.gpg] https://packages.fluentbit.io/debian/trixie trixie main" \
      > /etc/apt/sources.list.d/fluentbit.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends fluent-bit=${FLB_VERSION} && \
    ln -s /opt/fluent-bit/bin/fluent-bit /usr/local/bin/fluent-bit && \
    groupadd -g 2000 tpot && \
    useradd -u 2000 -g 2000 -M -d /nonexistent -s /usr/sbin/nologin tpot && \
    rm -rf /etc/fluent-bit/* && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
COPY dist/fluent-bit.yaml dist/inputs.yaml dist/tpot.lua /etc/fluent-bit/
COPY dist/entrypoint.sh /usr/local/bin/tpot-fluentbit-entrypoint.sh

STOPSIGNAL SIGTERM
USER 2000:2000
HEALTHCHECK CMD test -f /tmp/tpot-waiting || curl -fs http://127.0.0.1:2020/api/v1/uptime
ENTRYPOINT ["/usr/local/bin/tpot-fluentbit-entrypoint.sh"]
