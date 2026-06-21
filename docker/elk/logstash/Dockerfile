FROM docker.elastic.co/logstash/logstash:9.3.5

USER root

COPY dist/entrypoint.sh /usr/local/bin/tpot-logstash-entrypoint.sh
COPY dist/logstash.yml /usr/share/logstash/config/logstash.yml
COPY dist/pipelines.yml /usr/share/logstash/config/pipelines.yml
COPY dist/pipelines_sensor.yml /usr/share/logstash/config/pipelines_sensor.yml
COPY dist/tpot-template.json /usr/share/logstash/config/tpot-template.json
COPY dist/*.conf /usr/share/logstash/pipeline/

RUN microdnf install -y bzip2 && \
    microdnf clean all && \
    rm -rf /var/cache/dnf /var/cache/yum && \
    /usr/share/logstash/bin/logstash-plugin install logstash-output-syslog && \
    mkdir -p /etc/listbot && \
    curl -fsSL https://listbot.sicherheitstacho.eu/cve.yaml.bz2 | bunzip2 -c > /etc/listbot/cve.yaml && \
    curl -fsSL https://listbot.sicherheitstacho.eu/iprep.yaml.bz2 | bunzip2 -c > /etc/listbot/iprep.yaml && \
    chmod 755 /usr/local/bin/tpot-logstash-entrypoint.sh && \
    sed -i 's/^logstash:x:1000:1000:/logstash:x:2000:2000:/' /etc/passwd && \
    sed -i 's/^logstash:x:1000:/logstash:x:2000:/' /etc/group && \
    sed -i 's/^root:x:0:/root:x:0:logstash/' /etc/group && \
    chown -R logstash:logstash \
        /etc/listbot \
        /usr/share/logstash/config \
        /usr/share/logstash/data \
        /usr/share/logstash/pipeline

HEALTHCHECK --retries=10 CMD curl -s -XGET 'http://127.0.0.1:9600'

USER logstash
CMD ["/usr/local/bin/tpot-logstash-entrypoint.sh"]
