FROM alpine:3.17
#
# Include dist
COPY dist/ /root/dist/
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
	    py3-cryptography \
	    py3-ipaddr \
	    py3-beautifulsoup4 \
	    py3-dnspython \
	    py3-exifread \
	    py3-future \
	    py3-jaraco.classes \
	    py3-jaraco.context \
	    py3-jaraco.functools \
	    py3-lxml \
	    py3-mako \
	    py3-more-itertools \
	    py3-netaddr \
	    py3-networkx \
	    py3-openssl \
	    py3-pillow \
	    py3-portend \
	    py3-pypdf2 \
	    py3-phonenumbers \
            py3-pip \
	    py3-pysocks \
	    py3-requests \
	    py3-tempora \
	    py3-wheel \
	    py3-xlsxwriter \
	    py3-yaml \
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
    git clone --depth=1 -b v4.0 https://github.com/smicallef/spiderfoot /home/spiderfoot && \
    cd /home/spiderfoot && \
    pip3 install --upgrade pip && \
    cp /root/dist/requirements.txt . && \
    pip3 install --no-cache-dir -r requirements.txt && \ 
    mkdir -p /home/spiderfoot/.spiderfoot/logs && \
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
    rm -rf /var/cache/apk/* /home/spiderfoot/.git
#
# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:8080/spiderfoot/'
#
# Set user, workdir and start spiderfoot
USER spiderfoot:spiderfoot
WORKDIR /home/spiderfoot
CMD echo -n >> /home/spiderfoot/.spiderfoot/spiderfoot.db && exec /usr/bin/python3 sf.py -l 0.0.0.0:8080
