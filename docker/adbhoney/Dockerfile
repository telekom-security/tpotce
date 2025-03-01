FROM alpine:3.20 AS builder
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN	apk --no-cache -U upgrade && \
	apk --no-cache -U add \
		build-base \
		git \
		procps \
		py3-psutil \
		py3-requests \
		py3-pip \
		python3 && \
		pip3 install --break-system-packages pyinstaller && \
#
# Install adbhoney from git
    git clone https://github.com/t3chn0m4g3/ADBHoney /opt/adbhoney && \
    cd /opt/adbhoney && \
	git checkout 42a73cd8a82ddd4d137de70ac37b1a8b2e3e0119 && \
    cp /root/dist/adbhoney.cfg /opt/adbhoney && \
    sed -i 's/dst_ip/dest_ip/' /opt/adbhoney/adbhoney/core.py && \
    sed -i 's/dst_port/dest_port/' /opt/adbhoney/adbhoney/core.py && \
    pyinstaller adbhoney.spec
#
FROM alpine:3.20
RUN apk --no-cache -U upgrade
COPY --from=builder /opt/adbhoney/dist/adbhoney/ /opt/adbhoney/
#
# Set workdir and start adbhoney
STOPSIGNAL SIGINT
USER 2000:2000
WORKDIR /opt/adbhoney/
CMD ["./adbhoney"]
