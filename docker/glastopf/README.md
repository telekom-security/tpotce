[![](https://images.microbadger.com/badges/version/dtagdevsec/glastopf:1706.svg)](https://microbadger.com/images/dtagdevsec/glastopf:1706 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/dtagdevsec/glastopf:1706.svg)](https://microbadger.com/images/dtagdevsec/glastopf:1706 "Get your own image badge on microbadger.com")

# dockerized glastopf v3


[glastopf](https://github.com/glastopf/glastopf) is a python web application honeypot.

This repository contains the necessary files to create a *dockerized* version of glastopf v3.

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized glastopf and will be used to setup the docker image.  

The `glastopf.cfg` is tailored to fit the T-Pot environment.

The `supervisord.conf` is used to start glastopf under supervision of supervisord.

Using systemd, copy the `systemd/glastopf.service` to `/etc/systemd/system/glastopf.service` and start using

```
systemctl enable glastopf
systemctl start glastopf
```

This will make sure that the docker container is started with the appropriate permissions and port mappings. Further, it autostarts during boot.

By default all data will be stored in `/data/glastopf/` until the honeypot service will be restarted which is by default every 24 hours. If you want to keep data persistently simply edit the ``service`` file, find the line that contains ``clean.sh`` and set the option from ``off`` to ``on``. Be advised to establish some sort of log management if you wish to do so.

# Glastopf Dashboard

![Glastopf Dashboard](https://raw.githubusercontent.com/dtag-dev-sec/glastopf/master/doc/dashboard.png)
