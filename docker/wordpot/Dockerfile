FROM alpine:3.19
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk -U --no-cache add \
		build-base \
		git \
		libcap \
		py3-click \
		py3-flask \
		py3-itsdangerous \
		py3-jinja2 \
		py3-markupsafe \
		py3-pip \
		py3-ua-parser \
		py3-werkzeug \
		py3-yaml \
		python3 \
		python3-dev && \
#	     
# Install wordpot from GitHub and setup
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://github.com/t3chn0m4g3/wordpot && \
    cd wordpot && \
    git checkout 8fedeb1b74e1d225da2a7273656bf0afa84a20aa && \
    cp /root/dist/requirements.txt . && \
    pip3 install --break-system-packages -r requirements.txt && \
    setcap cap_net_bind_service=+ep $(readlink -f $(type -P python3)) && \
#
# Setup user, groups and configs
    addgroup -g 2000 wordpot && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 wordpot && \
    chown wordpot:wordpot -R /opt/wordpot && \
#
# Clean up
    apk del --purge build-base \
		git \
		python3-dev && \
    rm -rf /root/* /var/cache/apk/* /opt/wordpot/.git
#
# Start wordpot
STOPSIGNAL SIGINT
USER wordpot:wordpot
WORKDIR /opt/wordpot
CMD ["/usr/bin/python3","wordpot.py", "--host", "0.0.0.0", "--port", "80", "--title", "Wordpress"]
