FROM alpine

# Setup apt
RUN apk -U --no-cache add \
               build-base \
               git \
               linux-headers \
               python3 \
               python3-dev && \ 

# Setup Snare 
    git clone --depth=1 https://github.com/mushorg/snare /opt/snare && \
    cd /opt/snare/ && \
    pip3 install --no-cache-dir --upgrade pip setuptools && \
    pip3 install --no-cache-dir -r requirements.txt && \
    python3.6 clone.py --target http://example.com && \
    
# Clean up
    apk del --purge \
            build-base \
            linux-headers \
            python3-dev && \
    rm -rf /root/* && \
    rm -rf /tmp/* /var/tmp/* && \
    rm -rf /var/cache/apk/*

# Start snare
WORKDIR /opt/snare
CMD /usr/bin/python3.6 /opt/snare/snare.py --tanner tanner --debug true --no-dorks --auto-update false --host-ip 0.0.0.0 --port 80 --page-dir example.com
