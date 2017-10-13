# dockerized elasticpot


[elasticpot](https://github.com/schmalle/ElasticPot) elasticpot is a simple elastic search honeypot.

This repository contains the necessary files to create a *dockerized* version of elasticpot.

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized elasticpot and will be used to setup the docker image.  

The `supervisord.conf` is used to start elasticpot under supervision of supervisord.

Using systemd, copy the `systemd/elasticpot.service` to `/etc/systemd/system/elasticpot.service` and start using

```
systemctl enable elasticpot
systemctl start elasticpot
```

This will make sure that the docker container is started with the appropriate permissions and port mappings. Further, it autostarts during boot.

By default all data will be stored in `/data/elasticpot/` until the honeypot service will be restarted which is by default every 24 hours. If you want to keep data persistently simply edit the ``service`` file, find the line that contains ``clean.sh`` and set the option from ``off`` to ``on``. Be advised to establish some sort of log management if you wish to do so.

# ElasticPot Dashboard

![ElasticPot Dashboard](https://raw.githubusercontent.com/dtag-dev-sec/elasticpot/master/doc/dashboard.png)
