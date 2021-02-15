FROM alpine:3.10
#
# Get and install dependencies & packages
RUN apk -U --no-cache add \
            curl \
            git \
            npm \
            nodejs && \
    npm install npm@latest -g && \
    npm install -g grunt-cli http-server && \
#
# Install CyberChef 
    cd /root && \
    git clone https://github.com/gchq/cyberchef -b v9.27.0 && \
    cd cyberchef && \
    npm install && \
    grunt prod && \
    mkdir -p /opt/cyberchef && \
    mv build/prod/* /opt/cyberchef && \
    cd / && \
#
# Clean up
    apk del --purge git \
                    npm && \
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:8000'
#
# Set user, workdir and start cyberchef
USER nobody:nobody
WORKDIR /opt/cyberchef
CMD ["http-server", "-p", "8000"]
