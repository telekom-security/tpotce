[![](https://images.microbadger.com/badges/version/dtagdevsec/conpot:1706.svg)](https://microbadger.com/images/dtagdevsec/conpot:1706 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/dtagdevsec/conpot:1706.svg)](https://microbadger.com/images/dtagdevsec/conpot:1706 "Get your own image badge on microbadger.com")

# conpot

[ConPot](http://conpot.org/) is a low interactive server side Industrial Control Systems honeypot designed to be easy to deploy, modify and extend. By providing a range of common industrial control protocols we created the basics to build your own system, capable to emulate complex infrastructures to convince an adversary that he just found a huge industrial complex. To improve the deceptive capabilities, we also provided the possibility to server a custom human machine interface to increase the honeypots attack surface. The response times of the services can be artificially delayed to mimic the behaviour of a system under constant load. Because we are providing complete stacks of the protocols, Conpot can be accessed with productive HMI's or extended with real hardware. Conpot is developed under the umbrella of the [Honeynet Project](https://www.honeynet.org/) and on the shoulders of a couple of very big giants.

This repository contains the necessary files to create a *dockerized* version of conpot.

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized conpot and will be used to setup the docker image.  

The `supervisord.conf` is used to start conpot under supervision of supervisord.

Using systemd, copy the `systemd/conpot.service` to `/etc/systemd/system/conpot.service` and start using

```
systemctl enable conpot
systemctl start conpot
```

This will make sure that the docker container is started with the appropriate permissions and port mappings. Further, it autostarts during boot.

By default all data will be stored in `/data/conpot/` until the honeypot service will be restarted which is by default every 24 hours. If you want to keep data persistently simply edit the ``service`` file, find the line that contains ``clean.sh`` and set the option from ``off`` to ``on``. Be advised to establish some sort of log management if you wish to do so.

# ConPot Dashboard

![ConPot Dashboard](https://raw.githubusercontent.com/dtag-dev-sec/conpot/master/doc/dashboard.png)
