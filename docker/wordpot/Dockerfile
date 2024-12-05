FROM alpine:3.20 AS builder
#
# Include dist
COPY dist/ /root/dist/
#
# Install packages
RUN apk --no-cache -U upgrade && \
	apk --no-cache -U add \
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
		python3-dev
#	     
# Install wordpot from GitHub and setup
RUN mkdir -p /opt && \
    cd /opt/ && \
    git clone https://github.com/t3chn0m4g3/wordpot && \
    cd wordpot && \
    git checkout b2701cb6c7057ed2843187c0c425bb8c13845719 && \
    cp /root/dist/requirements.txt .
WORKDIR /opt/wordpot
RUN pip3 install --no-cache-dir --break-system-packages pyinstaller
RUN	pip3 install --no-cache-dir --break-system-packages -r requirements.txt
RUN pyinstaller wordpot.py \
		--hidden-import hpfeeds \
		--add-data "./wordpot/static:./wordpot/static" \
		--add-data "./wordpot/templates:./wordpot/templates" \
		--add-data "./wordpot/plugins:./wordpot/plugins" \
		--add-data "./wordpot.conf:./"
RUN ls -alR
#
FROM alpine:3.20
RUN apk --no-cache -U upgrade
COPY --from=builder /opt/wordpot/dist /opt/
#
# Start wordpot
STOPSIGNAL SIGINT
USER 2000:2000
WORKDIR /opt/wordpot/
CMD ["./wordpot", "--host", "0.0.0.0", "--port", "80", "--title", "Wordpress"]
