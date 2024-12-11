FROM alpine:3.20
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U add \
		build-base \
		libcap \
		libffi-dev \
		musl-dev \
		openssl \
		openssl-dev \
		py3-pip \
		python3 \
		python3-dev && \
#	     
# Install honeypots from GitHub and setup
    pip3 install --break-system-packages --no-cache-dir honeypots && \
    setcap cap_net_bind_service=+ep $(readlink -f $(type -P python3)) && \
#
# Setup user, groups and configs
    mkdir -p /etc/honeypots/ && \
    cp /root/dist/config.json /etc/honeypots/ && \
#
# Clean up
    apk del --purge \
    	build-base \
		libffi-dev \
		musl-dev \
		openssl-dev \
		py3-pip \
		python3-dev && \
	rm -rf /root/* \
#
# Start honeypots 
STOPSIGNAL SIGINT
USER 2000:2000
CMD python3 -E -m honeypots --setup all --config /etc/honeypots/config.json
