[![](https://images.microbadger.com/badges/version/dtagdevsec/honeytrap:1706.svg)](https://microbadger.com/images/dtagdevsec/honeytrap:1706 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/dtagdevsec/honeytrap:1706.svg)](https://microbadger.com/images/dtagdevsec/honeytrap:1706 "Get your own image badge on microbadger.com")

# dockerized honeytrap


[honeytrap](https://github.com/armedpot/honeytrap) is a low-interaction honeypot daemon for observing attacks against network services. In contrast to other honeypots, which often focus on malware collection, honeytrap aims for catching the initial exploit – It collects and further processes attack traces.

This repository contains the necessary files to create a *dockerized* version of honeytrap.

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.
For this setup, honeytrap is configured to use the logattacker module only.

The `Dockerfile` contains the blueprint for the dockerized honeytrap and will be used to setup the docker image.  

The `honeytrap.conf` is tailored to fit the T-Pot environment.

The `supervisord.conf` is used to start honeytrap under supervision of supervisord.

In case you want to run the dockerized honeytrap independently, you must modify the config files to match your environment and rebuild the docker image.

Using systemd, copy the `systemd/honeytrap.service` to `/etc/systemd/system/honeytrap.service` and start using

```
systemctl enable honeytrap
systemctl start honeytrap
```

This will make sure that the docker container is started with the appropriate rights and iptables forwards are implemented. Further, it autostarts during boot.
In the T-Pot setup, some ports are excluded as they need to be reserved for other honeypot daemons running in parallel.

By default all data will be stored in `/data/honeytrap/` until the honeypot service will be restarted which is by default every 24 hours. If you want to keep data persistently simply edit the ``service`` file, find the line that contains ``clean.sh`` and set the option from ``off`` to ``on``. Be advised to establish some sort of log management if you wish to do so.

# Honeytrap Dashboard

![Honeytrap Dashboard](https://raw.githubusercontent.com/dtag-dev-sec/honeytrap/master/doc/dashboard.png)
