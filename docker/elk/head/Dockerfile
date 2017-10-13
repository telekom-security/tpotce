# Elasticsearch-head Dockerfile by MO
#
# VERSION 17.06 
FROM alpine
MAINTAINER MO

# Setup env and apt
RUN apk -U upgrade && \
    apk add bash curl nodejs nodejs-npm git procps && \

# Get and install packages
    mkdir -p /usr/src/app/ && \
    cd /usr/src/app/ && \
    git clone https://github.com/mobz/elasticsearch-head . && \
    npm install http-server && \
    sed -i 's/\"http\:\/\/localhost\:9200\"/\"https\:\/\/\<FQDN\>\:64297\/es\/\"/' /usr/src/app/_site/app.js && \

# Setup user, groups and configs
    addgroup -g 2000 head && \
    adduser -S -H -s /bin/bash -u 2000 -D -g 2000 head && \
    chown -R head:head /usr/src/app/ && \

# Clean up
    apk del git

# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:9100'

# Start elasticsearch-head
USER head
WORKDIR /usr/src/app
CMD ["node_modules/http-server/bin/http-server", "_site", "-p", "9100"]
