FROM docker.elastic.co/elasticsearch/elasticsearch:9.3.5

USER root

COPY dist/elasticsearch.yml /usr/share/elasticsearch/config/elasticsearch.yml

RUN sed -i 's/^elasticsearch:x:1000:1000:/elasticsearch:x:2000:2000:/' /etc/passwd && \
    sed -i 's/^elasticsearch:x:1000:/elasticsearch:x:2000:/' /etc/group && \
    chown -R elasticsearch:elasticsearch \
        /usr/share/elasticsearch/config \
        /usr/share/elasticsearch/data \
        /usr/share/elasticsearch/logs

HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:9200/_cat/health'

USER elasticsearch
