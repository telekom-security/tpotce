FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
#
# Install packages
RUN apt-get update -y && \
    apt-get upgrade -y && \
    apt-get install -y \
		build-essential \
		cargo \
		cleo \
		git \
		libcap2 \
		libcap2-bin \
		libcurl4t64 \
		libcurl4-gnutls-dev \
		libffi8 \
		libffi-dev \
		libssl-dev \
		python3-pip \
		python3 \
		python3-dev \
		rust-all && \
	pip3 install --no-cache-dir --break-system-packages \
		poetry==1.8.3 \
		pycurl && \
#	     
# Install log4pot from GitHub and setup
    mkdir -p /opt /var/log/log4pot && \
    cd /opt/ && \
    git clone https://github.com/thomaspatzke/Log4Pot && \
    cd Log4Pot && \
    git checkout 5002b1fe0f82359ef32dbc3a899e8a701dc3256e && \
    sed -i 's#"type": logtype,#"reason": logtype,#g' log4pot-server.py && \
	rm poetry.lock && \
    poetry --no-cache --without=dev install && \
    setcap cap_net_bind_service=+ep $(readlink -f $(which python3)) && \
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
		libcurl4-gnutls-dev \
		libffi-dev \
		libssl-dev \
		python3-dev \
		python3-pip \
		rust-all && \
    apt-get autoremove -y --purge && \
    apt-get clean && \
	rm -rf /var/lib/apt/lists/* \
		/tmp/* \
		/var/tmp/* \
		/root/.cache \
		/opt/Log4Pot/.git
#
# Start log4pot
STOPSIGNAL SIGINT
USER log4pot:log4pot
WORKDIR /opt/Log4Pot/
CMD ["/usr/bin/python3","log4pot-server.py","--port","8080","--log","/var/log/log4pot/log/log4pot.log","--payloader","--download-dir","/var/log/log4pot/payloads/","--download-timeout","15","--response","/opt/Log4Pot/responses/sap-netweaver.html"]
