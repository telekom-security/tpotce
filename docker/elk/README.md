# dockerized elk stack


[elk](http://www.elasticsearch.org/overview/) is a stack combining elasticsearch, logstash and the kibana dashboard. It is used to structure and vizualize data in realtime.

This repository contains the necessary files to create a *dockerized* version of the elk stack.

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized elk stack and will be used to setup the docker image.  

Further, `elasticsearch.yml`, `logstash.conf`, `elkbase.tar.gz`, `elk.ico` and `kibana.svg`,  are all tailored to fit the T-Pot environment.

The `supervisord.conf` is used to start elk under supervision of supervisord.

Using systemd, copy the `systemd/elk.service` to `/etc/systemd/system/elk.service` and start using

```
systemctl enable elk
systemctl start elk
```

This will make sure that the docker container is started with the appropriate permissions and port mappings. Further, it autostarts during boot.

Starting with T-Pot 16.10 you can simply access the kibana dashboard by browsing to ``https://<your.ip>:64297`` and enter your web user credentials.

Note: The kibana dashboard can be customized to fit your needs.

By default all data will be persistently stored in `/data/elk/`. Indexed events older than 90 days will be deleted. You can adjust this behavior in `/etc/crontab` to fit your needs, but be advised to provide enough RAM and free disk-space if you wish to do so.

# T-Pot Dashboard

![T-Pot Dashboard](https://raw.githubusercontent.com/dtag-dev-sec/elk/master/doc/dashboard.png)
