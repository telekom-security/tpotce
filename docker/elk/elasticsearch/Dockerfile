ARG ES_VER=9.5.4
FROM docker.elastic.co/elasticsearch/elasticsearch:${ES_VER}

# Kept in the image, update.sh reads it to tell whether an update changes the
# Elasticsearch version - the data upgrade that comes with it cannot be undone
ARG ES_VER
ENV ES_VER=${ES_VER}

USER root

COPY dist/elasticsearch.yml /usr/share/elasticsearch/config/elasticsearch.yml

RUN sed -i 's/^elasticsearch:x:1000:1000:/elasticsearch:x:2000:2000:/' /etc/passwd && \
    sed -i 's/^elasticsearch:x:1000:/elasticsearch:x:2000:/' /etc/group && \
    [ "$(id -u elasticsearch):$(id -g elasticsearch)" = "2000:2000" ] && \
    chown -R elasticsearch:elasticsearch \
        /usr/share/elasticsearch/config \
        /usr/share/elasticsearch/data \
        /usr/share/elasticsearch/logs

HEALTHCHECK --start-period=5m --retries=10 CMD curl -s -XGET 'http://127.0.0.1:9200/_cat/health'

USER elasticsearch
