FROM alpine:3.14
#
# Get and install dependencies & packages
RUN apk -U --no-cache add \
            build-base \
            curl \
            git \
	    jpeg-dev \
	    libffi-dev \
            libxml2 \
            libxml2-dev \
            libxslt \
            libxslt-dev \
	    musl \
	    musl-dev \
	    openjpeg-dev \
            openssl \
            openssl-dev \
            python3 \
            python3-dev \
            py3-pip \
            swig \
	    tinyxml \
	    tinyxml-dev \
	    zlib-dev && \
#
# Setup user
    addgroup -g 2000 spiderfoot && \
    adduser -S -s /bin/ash -u 2000 -D -g 2000 spiderfoot && \
#
# Install spiderfoot 
    git clone --depth=1 -b v3.4 https://github.com/smicallef/spiderfoot /home/spiderfoot && \
    cd /home/spiderfoot && \
    pip3 install --upgrade pip && \
    pip3 install --no-cache-dir wheel && \ 
    pip3 install --no-cache-dir -r requirements.txt && \ 
    chown -R spiderfoot:spiderfoot /home/spiderfoot && \
    sed -i "s#'root': '\/'#'root': '\/spiderfoot'#" /home/spiderfoot/sf.py && \
    sed -i "s#'root', '\/'#'root', '\/spiderfoot'#" /home/spiderfoot/sf.py && \
#
# Clean up
    apk del --purge build-base \
                    gcc \
                    git \
		    libffi-dev \
                    libxml2-dev \
                    libxslt-dev \
		    musl-dev \
                    openssl-dev \
                    python3-dev \
		    swig \
		    tinyxml-dev && \
    rm -rf /var/cache/apk/*
#
# Healthcheck
#HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:8080'
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:8080/spiderfoot/'
#
# Set user, workdir and start spiderfoot
USER spiderfoot:spiderfoot
WORKDIR /home/spiderfoot
CMD ["/usr/bin/python3.9", "sf.py","-l", "0.0.0.0:8080"]
