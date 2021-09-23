FROM debian:buster-slim
#
# Include dist
ADD dist/ /root/dist/
#
# Get and install dependencies & packages
RUN apt-get update -y && \
    apt-get install -y \
            build-essential \
            git \
            libffi-dev \
            openssl \
            libssl-dev \
            python2 \
            python2-dev \
            python-pip \
            python-setuptools && \
#
# Setup user
    addgroup -gid 2000 rdpy && \
    adduser --system --no-create-home --shell /bin/bash --uid 2000 --disabled-password --disabled-login --gid 2000 rdpy && \
#
# Install deps 
    pip install --no-cache-dir --upgrade cffi && \
    pip install --no-cache-dir \
                hpfeeds \
                twisted \
                pyopenssl \
                qt4reactor \
		rsa==4.5 \
                service_identity \
                pyasn1 && \
#
# Install rdpy from git
    mkdir -p /opt && \
    cd /opt && \
    git clone https://github.com/t3chn0m4g3/rdpy && \
    cd rdpy && \
    git checkout 1d2a4132aefe0637d09cac1a6ab83ec5391f40ca && \
    python setup.py install && \
#
# Setup user, groups and configs
    cp /root/dist/* /opt/rdpy/ && \
    chown rdpy:rdpy -R /opt/rdpy/* && \
    mkdir -p /var/log/rdpy && \
#
# Clean up
    apt-get purge -y build-essential \
                    git \
                    libffi-dev \
                    libssl-dev \
                    python2-dev \
                    python-pip && \
    apt-get clean -y && \
    apt-get autoremove --purge -y && \
    apt-get install python-enum34 python-ipaddress python-six -y && \
    rm -rf /root/* /var/lib/apt/lists/* /tmp/* /var/tmp/*		    
#
# Start rdpy
USER rdpy:rdpy
CMD exec /usr/bin/python2 -i /usr/local/bin/rdpy-rdphoneypot.py /opt/rdpy/$(shuf -i 1-3 -n 1) >> /var/log/rdpy/rdpy.log
