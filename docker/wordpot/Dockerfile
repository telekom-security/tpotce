FROM alpine:3.17
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
	     py3-werkzeug \
             python3 \
             python3-dev && \
#	     
# Install wordpot from GitHub and setup
    mkdir -p /opt && \
    cd /opt/ && \
    git clone https://github.com/Will-777/wordpot2 && \
    cd wordpot2 && \
    git checkout e93a2e00d84d280b0acd58ba6889b4bee8a6e4d2 && \
#    cp /root/dist/views.py /opt/wordpot2/wordpot/views.py && \
    cp /root/dist/requirements.txt . && \
    pip3 install -r requirements.txt && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.10 && \
#
# Setup user, groups and configs
    addgroup -g 2000 wordpot && \
    adduser -S -H -s /bin/ash -u 2000 -D -g 2000 wordpot && \
    chown wordpot:wordpot -R /opt/wordpot2 && \
#
# Clean up
    apk del --purge build-base \
                    git \
		    python3-dev && \
    rm -rf /root/* /var/cache/apk/* /opt/wordpot2/.git
#
# Start wordpot
STOPSIGNAL SIGINT
USER wordpot:wordpot
WORKDIR /opt/wordpot2
CMD ["/usr/bin/python3","wordpot2.py", "--host", "0.0.0.0", "--port", "80", "--title", "Wordpress"]
