FROM alpine:3.20 AS builder
#
# Install packages
RUN apk --no-cache -U upgrade && \
	apk --no-cache -U add \
	build-base \
	git \	
	python3 \
    py3-pip
#
RUN	mkdir -p /opt && \
	cd /opt/ && \
	git clone https://github.com/t3chn0m4g3/miniprint
WORKDIR /opt/miniprint
RUN pip3 install --break-system-packages pyinstaller
RUN	pip3 install --break-system-packages -r requirements.txt
RUN pyinstaller server.py \
		--add-data "./fake-files:./fake-files" \
		--add-data "./uploads:./uploads"
#
FROM alpine:3.20
RUN apk --no-cache -U upgrade
COPY --from=builder /opt/miniprint/dist/* /opt/miniprint/
COPY --from=builder /opt/miniprint/fake-files/ /opt/miniprint/fake-files/
COPY --from=builder /opt/miniprint/uploads/ /opt/miniprint/uploads/
#
# Start miniprint
STOPSIGNAL SIGINT
USER 2000:2000
WORKDIR /opt/miniprint/
CMD ["./server", "--bind", "0.0.0.0", "--log-file", "/opt/miniprint/log/miniprint.json"]
