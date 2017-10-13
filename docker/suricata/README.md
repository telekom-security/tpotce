[![](https://images.microbadger.com/badges/version/dtagdevsec/suricata:1706.svg)](https://microbadger.com/images/dtagdevsec/suricata:1706 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/dtagdevsec/suricata:1706.svg)](https://microbadger.com/images/dtagdevsec/suricata:1706 "Get your own image badge on microbadger.com")

# dockerized suricata


[suricata](http://suricata-ids.org/) is a Network IDS, IPS and Network Security Monitoring engine.

This repository contains the necessary files to create a *dockerized* version of suricata.

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized suricata and will be used to setup the docker image.  

The `suricata.yaml` is tailored to fit the T-Pot environment.

The `supervisord.conf` is used to start suricata under supervision of supervisord.

Using systemd, copy the `systemd/suricata.service` to `/etc/systemd/system/suricata.service` and start using

```
systemctl enable suricata
systemctl start suricata
```

This will make sure that the docker container is started with the appropriate permissions and port mappings. Further, it autostarts during boot.

By default all data will be stored in `/data/suricata/` until the service will be restarted which is by default every 24 hours. If you want to keep data persistently simply edit the ``service`` file, find the line that contains ``clean.sh`` and set the option from ``off`` to ``on``. Be advised to establish some sort of log management if you wish to do so.

# Suricata Dashboard

![Suricata Dashboard](https://raw.githubusercontent.com/dtag-dev-sec/suricata/master/doc/dashboard.png)
