FROM ubuntu:22.04
ENV DEBIAN_FRONTEND noninteractive
#
# Install packages
RUN apt-get update -y && \
    apt-get install -y \
             build-essential \
	     cargo \
	     cleo \
             git \
	     libcap2 \
	     libcap2-bin \
	     libcurl4 \
	     libcurl4-nss-dev \
	     libffi7 \
	     libffi-dev \
	     libssl-dev \
	     python3-pip \
             python3 \
             python3-dev \
             rust-all && \
     pip3 install --upgrade pip && \
     pip3 install poetry pycurl && \
#	     
# Install log4pot from GitHub and setup
    mkdir -p /opt /var/log/log4pot && \
    cd /opt/ && \
    git clone https://github.com/thomaspatzke/Log4Pot && \
    cd Log4Pot && \
    # git checkout fac539f470217347e51127c635f16749a887c0ac && \
    git checkout e224c0f786efb68b4aab892e69857e379b75b0c6 && \
    sed -i 's#"type": logtype,#"reason": logtype,#g' log4pot-server.py && \
    poetry install && \
    setcap cap_net_bind_service=+ep /usr/bin/python3.10 && \
#
# Setup user, groups and configs
    addgroup --gid 2000 log4pot && \
    adduser --system --no-create-home --shell /bin/bash -uid 2000 --disabled-password --disabled-login -gid 2000 log4pot && \
    chown log4pot:log4pot -R /opt/Log4Pot && \
#
# Clean up
     apt-get purge -y build-essential \
                    cargo \
                    git \
		    libffi-dev \
		    libssl-dev \
		    python3-dev \
		    rust-all && \
    apt-get autoremove -y --purge && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* /root/.cache /opt/Log4Pot/.git
#
# Start log4pot
STOPSIGNAL SIGINT
USER log4pot:log4pot
WORKDIR /opt/Log4Pot/
CMD ["/usr/bin/python3","log4pot-server.py","--port","8080","--log","/var/log/log4pot/log/log4pot.log","--payloader","--download-dir","/var/log/log4pot/payloads/","--download-timeout","15","--response","/opt/Log4Pot/responses/sap-netweaver.html"]
