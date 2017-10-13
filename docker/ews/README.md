[![](https://images.microbadger.com/badges/version/dtagdevsec/ewsposter:1706.svg)](https://microbadger.com/images/dtagdevsec/ewsposter:1706 "Get your own version badge on microbadger.com") [![](https://images.microbadger.com/badges/image/dtagdevsec/ewsposter:1706.svg)](https://microbadger.com/images/dtagdevsec/ewsposter:1706 "Get your own image badge on microbadger.com")

# dockerized ewsposter


[ewsposter](https://github.com/dtag-dev-sec/ews) is a python application that collects information from multiple honeypot sources and posts it to central collection services like the DTAG early warning system and hpfeeds. 

This dockerized version is part of the **[T-Pot community honeypot](http://github.com/dtag-dev-sec/tpotce)** of Deutsche Telekom AG. 

The `Dockerfile` contains the blueprint for the dockerized ewsposter and will be used to setup the docker image.  

The `ews.cfg` is tailored to fit the T-Pot environment.

The `supervisord.conf` is used to start ewsposter under supervision of supervisord. 
