FROM node:10.24.1-alpine3.11 as builder
#
# Install CyberChef 
RUN apk -U --no-cache add git
RUN chown -R node:node /srv
RUN npm install -g grunt-cli
WORKDIR /srv
USER node
RUN git clone https://github.com/gchq/cyberchef -b v9.32.3 .
ENV NODE_OPTIONS=--max_old_space_size=2048
RUN npm install
RUN grunt prod
#
# Move from builder
FROM alpine:3.15
#
RUN apk -U --no-cache add \
      curl \
      npm && \
      npm install -g http-server && \
#
# Clean up
    rm -rf /root/* && \
    rm -rf /var/cache/apk/*
#
COPY --from=builder /srv/build/prod /opt/cyberchef
#
# Healthcheck
HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:8000'
#
# Set user, workdir and start cyberchef
USER nobody:nobody
WORKDIR /opt/cyberchef
CMD ["http-server", "-p", "8000"]
