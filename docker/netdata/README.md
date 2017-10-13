[![](https://images.microbadger.com/badges/version/dtagdevsec/netdata:1706.svg)](https://microbadger.com/images/dtagdevsec/netdata:1706 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/dtagdevsec/netdata:1706.svg)](https://microbadger.com/images/dtagdevsec/netdata:1706 "Get your own image badge on microbadger.com")

# dockerized netdata


[netdata](http://my-netdata.io/) netdata is a system for distributed real-time performance and health monitoring. It provides unparalleled insights, in real-time, of everything happening on the system it runs (including applications such as web, or database servers), using modern interactive web dashboards. netdata is fast and efficient, designed to permanently run on all systems (physical & virtual servers, containers, IoT devices), without disrupting their core function.

This repository contains the necessary files to create a *dockerized* version of netdata.

This dockerized version is part of the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)** of Deutsche Telekom AG.

The `Dockerfile` contains the blueprint for the dockerized netdata and will be used to setup the docker image.  

Using systemd, copy the `systemd/netdata.service` to `/etc/systemd/system/netdata.service` and start using

```
systemctl enable netdata
systemctl start netdata
```

This will make sure that the docker container is started with the appropriate permissions and port mappings. Further, it autostarts during boot.

# Netdata Dashboard

![Netdata Dashboard](https://raw.githubusercontent.com/dtag-dev-sec/netdata/master/doc/dashboard.png)
