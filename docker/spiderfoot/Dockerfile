FROM alpine
MAINTAINER MO

# Get and install dependencies & packages
RUN apk -U upgrade && \
    apk add bash build-base curl git libxml2-dev libxslt-dev openssl-dev procps python-dev py-lxml py-netaddr py-mako py-pip py-setuptools py-requests swig && \
    apk -U add --repository http://dl-3.alpinelinux.org/alpine/edge/testing/ \
            py-beautifulsoup4 py-cherrypy && \
    pip install m2crypto && \

# Setup user
    addgroup -g 2000 spiderfoot && \
    adduser -S -s /bin/bash -u 2000 -D -g 2000 spiderfoot && \

# Install spiderfoot 
    git clone https://github.com/smicallef/spiderfoot -b v2.11.0-final /home/spiderfoot && \
    chown -R spiderfoot:spiderfoot /home/spiderfoot && \
    sed -i "s#'__docroot': ''#'__docroot': '\/spiderfoot'#" /home/spiderfoot/sf.py && \
    sed -i 's#raise cherrypy.HTTPRedirect("\/")#raise cherrypy.HTTPRedirect("\/spiderfoot")#' /home/spiderfoot/sfwebui.py && \

# Clean up
    apk del build-base git libxml2-dev libxslt-dev openssl-dev python-dev py-pip py-setuptools && \
    apk add openssl libxml2 libxslt python && \
    rm -rf /var/cache/apk/*

# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:8080'

# Set user, workdir and start spiderfoot
USER spiderfoot
WORKDIR /home/spiderfoot
CMD ["/usr/bin/python", "sf.py", "0.0.0.0:8080"]
