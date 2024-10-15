FROM alpine:3.19
#
# Install packages
RUN apk --no-cache -U add \
	git \	
	python3 \
    py3-pip && \
	mkdir -p /opt && \
	cd /opt/ && \
	git clone https://github.com/t3chn0m4g3/miniprint && \
	cd miniprint && \
	pip3 install --break-system-packages -r requirements.txt && \
#
# Setup user, groups and configs
	addgroup -g 2000 miniprint && \
	adduser -S -H -s /bin/ash -u 2000 -D -g 2000 miniprint && \
#
# Clean up
	apk del --purge git && \
    rm -rf /root/* /var/cache/apk/* /opt/miniprint/.git
#
# Start miniprint
STOPSIGNAL SIGINT
USER miniprint:miniprint
WORKDIR /opt/miniprint/
CMD ["/usr/bin/python3", "./server.py", "--bind", "0.0.0.0", "--log-file", "log/miniprint.json"]
