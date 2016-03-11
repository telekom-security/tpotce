# T-Pot 16.03 Image Creator

This repository contains the necessary files to create the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)**  ISO image.
The image can then be used to install T-Pot on a physical or virtual machine.

Last year we released
[T-Pot 15.03](http://dtag-dev-sec.github.io/mediator/feature/2015/03/17/concept.html)
as open source and we received lots of positive feedback and naturally feature requests which encouraged us to continue development and share our work as open source and are proud to present to you ...

# T-Pot 16.03

T-Pot 16.03 is based on

[docker](https://www.docker.com/)

and includes dockerized versions of the following honeypots

* [conpot](http://conpot.org/),
* [cowrie](http://www.micheloosterhof.com/cowrie/),
* [dionaea](https://github.com/DinoTools/dionaea),
* [elasticpot](https://github.com/schmalle/ElasticPot),
* [emobility](https://github.com/dtag-dev-sec/emobility),
* [glastopf](http://glastopf.org/) and
* [honeytrap](https://github.com/armedpot/honeytrap/)

Furthermore we use the following tools

* [suricata](http://suricata-ids.org/) a Network Security Monitoring engine and the
* [ELK stack](https://www.elastic.co/videos) to beautifully visualize all the events captured by T-Pot.


# TL;DR
1. Meet the [system requirements](#requirements). The T-Pot installation needs at least 4 GB RAM and 64 GB free disk space as well as a working internet connection.
2. Download the [tpotce.iso](http://community-honeypot.de/tpotce.iso) or [create it yourself](#createiso).
3. Install the system in a [VM](#vm) or on [physical hardware](#hw) with [internet access](#placement).
4. Enjoy your favorite beverage - [watch](http://sicherheitstacho.eu/?peers=communityPeers) and [analyze](#kibana).

-

<!--
To Do
We have created a nice [installation video](https://youtu.be/dWbJS_9sFNE) for you in case you run into problems.

In case you already have an Ubuntu 14.04.x running in your datacenter and are unable to install from an ISO image, we have created a [script](http://dtag-dev-sec.github.io/mediator/feature/2015/05/11/t-pot-autoinstall.html) that converts your Ubuntu base install into a full-fledged T-Pot within just a couple of minutes.
-->

# Table of Contents
- [Changelog](#changelog)
- [Technical Concept](#concept)
- [System Requirements](#requirements)
- [Installation](#installation)
  - [Prebuilt ISO Image](#prebuilt)
  - [Create your own ISO Image](#createiso)
  - [Running in a VM](#vm)
  - [Running on Hardware](#hardware)
  - [First Run](#firstrun)
  - [System Placement](#placement)
- [Options](#options)
  - [Enabling SSH](#ssh)
  - [Kibana Dashboard](#kibana)
  - [Maintenance](#maintenance)
  - [Community Data Submission](#submission)
- [Roadmap](#roadmap)
- [Disclaimer](#disclaimer)
- [FAQ](#faq)
- [Contact](#contact)
- [Licenses](#licenses)
- [Credits](#credits)
- [Stay tuned](#staytuned)
- [Fun Fact](#funfact)

<a name="background"></a>
# Changelog
- **Docker** was updated to the latest **1.10.x** release
- **ELK** was updated to the latest **Kibana 4.4.x**, **Elasticsearch 2.2.x** and **Logstash 2.2.x** releases.
- More than **100 Visualizations** compiled to 12 individual **Dashboards** for every honeypot now allow you to monitor the *honeypot events* captured on your T-Pot installation; a huge improvement over T-Pot 15.03 which was only capable of showing Suricata NSM events.
- Thanks to Kibana 4.x SSH port forwarding can now utilize any user defined local port

        ssh -p 64295 -l tsec -N -L4711:127.0.0.1:64296 <yourHoneypotIPaddress>

- **IP to AS Lookups** are now provided within Kibana dashboard, as well as some smart links to research IP reputation, Suricata Rules or AS information when in Discover mode.
- **ElasticSearch** indexes will now be kept for <=90 days, the time period may be adjusted in `/etc/crontab`.
- **Suricata** was updated to the latest **3.0** version including the latest **Emerging Threats** community ruleset.
- **P0f** is now part of the Suricata container, passively fingerprinting and guessing the involving OS.
- **Conpot**, **ElasticPot** and **eMobility** are being introduced as new honeypots in T-Pot.
- **Cowrie** replaces **Kippo** as SSH honeypot since it offers huge improvements over Kippo such as *(SFTP-support, exec-support, SSH-tunneling, advanced logging, JSON logging, etc.)*.
- With **Conpot** and **eMobility** we are now offering an experimental **Industrial Installation Option**.
- **T-Pot Image Creator** was completely rewritten to offer a more convenient experience for creating your personal T-Pot image (*802.1x authentication, proxy support, public key for SSH and pre defined NTP server*). Docker images can be preloaded using the experimental **`getimages.sh`** script and will be exported to the installation image.
- T-Pot itself and all of its containers are now based on **Ubuntu Server 14.04.4 LTS** and thus automatically benefit from the latest features introduced by Cannonical for Ubuntu Server.
- **Docker** containers are now storing important log data outside the container in `/data/<container-name>` allowing easy access from the host and improving container startup and restart speed.
- The **upstart** scripts have been rewritten to support storing data on the host either volatile (*default*) or persistent (`/data/persistence.on`).
- Depending on the honeypot **EWS-Poster** now supports extracting some logging information as JSON.
- The **`/usr/bin/backup_elk.sh`** allows you to backup all ElasticSearch indexes including `.kibana` and `logstash` which contain all information to restore your data on a freshly installed machine simply by entering `tar xvfz <backup-name>.tgz -C /`.
- The **`enable_ssh.sh`** script has been removed and is now part of a more convenient **`2fa_enable.sh`** script.
- Size limits for the `/data` have been lifted and swap space is now 8 GB.
- The number of **installation reboots** has been reduced to **2**. The first to finish the initial Ubuntu Server installation and the second after setting up T-Pot and its dependencies.
- Some packages are now be installed directly from the installation image instead of downloading them.


<a name="concept"></a>
# Technical Concept

T-Pot is based on Ubuntu Server 14.04.4 LTS.
The honeypot daemons as well as other support components being used have been paravirtualized using [docker](http://docker.io).
This allowed us to run multiple honeypot daemons on the same network interface without problems make the entire system very low maintenance. <br>The encapsulation of the honeypot daemons in docker provides a good isolation of the runtime environments and easy update mechanisms.

In T-Pot we combine the dockerized honeypots
[conpot](http://conpot.org/),
[cowrie](http://www.micheloosterhof.com/cowrie/),
[dionaea](https://github.com/DinoTools/dionaea),
[elasticpot](https://github.com/schmalle/ElasticPot),
[emobility](https://github.com/dtag-dev-sec/emobility),
[glastopf](http://glastopf.org/) and
[honeytrap](https://github.com/armedpot/honeytrap/) with
[suricata](http://suricata-ids.org/) a Network Security Monitoring engine and the
[ELK stack](https://www.elastic.co/videos) to beautifully visualize all the events captured by T-Pot. Events will be correlated by our own data submission tool [ewsposter](https://github.com/dtag-dev-sec/ews) which also supports Honeynet project hpfeeds honeypot data sharing.

![Architecture](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/architecture.png)

All data in docker is volatile. Once a docker container crashes, all data produced within its environment is gone and a fresh instance is restarted. Hence, for some data that needs to be persistent, i.e. config files, we have a persistent storage **`/data/`** on the host in order to make it available and persistent across container or system restarts.<br>
Important log data is now also stored outside the container in `/data/<container-name>` allowing easy access to logs from within the host and. The **upstart** scripts have been adjusted to support storing data on the host either volatile (*default*) or persistent (`/data/persistence.on`).

Basically, what happens when the system is booted up is the following:

- start host system
- start all the necessary services (i.e. docker-engine)
- start all docker containers (honeypots, nms, elk)

Within the T-Pot project, we provide all the tools and documentation necessary to build your own honeypot system and contribute to our [community data view](http://sicherheitstacho.eu/?peers=communityPeers), a separate channel on our  [Sicherheitstacho](http://sicherheitstacho.eu) that is powered by T-Pot community data.

The source code and configuration files are stored in individual GitHub repositories, which are linked below. The docker images are tailored to be run in this environment. If you want to run the docker images separately, make sure you study the upstart scripts, as they provide an insight on how we configured them.

The individual docker configurations etc. we used can be found here:

- [conpot](https://github.com/dtag-dev-sec/conpot)
- [cowrie](https://github.com/dtag-dev-sec/cowrie)
- [dionaea](https://github.com/dtag-dev-sec/dionaea)
- [elasticpot](https://github.com/dtag-dev-sec/elasticpot)
- [elk-stack](https://github.com/dtag-dev-sec/elk)
- [emobility](https://github.com/dtag-dev-sec/emobility)
- [glastopf](https://github.com/dtag-dev-sec/glastopf)
- [honeytrap](https://github.com/dtag-dev-sec/honeytrap)
- [suricata](https://github.com/dtag-dev-sec/suricata)

<a name="requirements"></a>
# System Requirements
Depending on your installation type, whether you install on [real hardware](#hardware) or in a [virtual machine](#vm), make sure your designated T-Pot system meets the following requirements:

##### T-Pot Installation (Cowrie, Dionaea, ElasticPot, Glastopf, Honeytrap, ELK, Suricata+P0f)
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:

- 4 GB RAM (6-8 GB recommended)
- 64 GB disk (128 GB SSD recommended)
- Network via DHCP
- A working internet connection

##### Sensor Installation (Cowrie, Dionaea, ElasticPot, Glastopf, Honeytrap)
This installation type is currently only available via [ISO Creator](https://github.com/dtag-dev-sec).
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:

- 3 GB RAM (4-6 GB recommended)
- 64 GB disk (64 GB SSD recommended)
- Network via DHCP
- A working internet connection

##### Industrial Installation (ConPot, eMobility, ELK, Suricata+P0f)
This installation type is currently only available via [ISO Creator](https://github.com/dtag-dev-sec) and remains experimental.
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:

- 4 GB RAM (8 GB recommended)
- 64 GB disk (128 GB SSD recommended)
- Network via DHCP
- A working internet connection

##### Everything Installation (Everything)
This installation type is currently only available via [ISO Creator](https://github.com/dtag-dev-sec).
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:

- 8 GB RAM
- 128 GB disk or larger (128 GB SSD or larger recommended)
- Network via DHCP
- A working internet connection

<a name="installation"></a>
# Installation
The installation of T-Pot is straight forward. Please be advised that you should have an internet connection up and running as all all the docker images for the chosen installation type need to be pulled from docker hub.

Firstly, decide if you want to download our prebuilt installation ISO image [tpotce.iso](http://community-honeypot.de/tpotce.iso) ***or*** [create it yourself](#createiso).

Secondly, decide where you want to let the system run: [real hardware](#hardware) or in a [virtual machine](#vm)?

<a name="prebuilt"></a>
## Prebuilt ISO Image
We provide an installation ISO image for download (~600MB), which is created using the same [tool](https://github.com/dtag-dev-sec/tpotce) you can use yourself in order to create your own image. It will basically just save you some time downloading components and creating the ISO image.
You can download the prebuilt installation image [here](http://community-honeypot.de/tpotce.iso) and jump to the [installation](#vm) section. The ISO image is hosted by our friends from [Strato](http://www.strato.de) / [Cronon](http://www.cronon.de).

    shasum tpotce.iso
    3b8f15eba2a478b106b202726661ce75c8fe7acc tpotce.iso

<a name="createiso"></a>
## Create your own ISO Image
For transparency reasons and to give you the ability to customize your install, we provide you the [ISO Creator](https://github.com/dtag-dev-sec/tpotce) that enables you to create your own ISO installation image.

**Requirements to create the ISO image:**
- Ubuntu 14.04.4 or newer as host system (others *may* work, but remain untested)
- 4GB of free memory  
- 32GB of free storage
- A working internet connection

**How to create the ISO image:**

1. Clone the repository and enter it.

        git clone https://github.com/dtag-dev-sec/tpotce.git
        cd tpotce

2. Invoke the script that builds the ISO image.
The script will download and install dependencies necessary to build the image on the invoking machine. It will further download the ubuntu base image (~600MB) which T-Pot is based on.

        sudo ./makeiso.sh

After a successful build, you will find the ISO image `tpot.iso` in your directory.

<a name="vm"></a>
## Running in VM
You may want to run T-Pot in a virtualized environment. The virtual system configuration depends on your virtualization provider.

We successfully tested T-Pot with [VirtualBox](https://www.virtualbox.org) and [VMWare](http://www.vmware.com) with just little modifications to the default machine configurations.

It is important to make sure you meet the [system requirements](#requirements) and assign a virtual harddisk >=64 GB, >=4 GB RAM and bridged networking to T-Pot.

You need to enable promiscuous mode for the network interface for suricata to work properly. Make sure you enable it during configuration.

If you want to use a wifi card as primary NIC for T-Pot, please remind that not all network interface drivers support all wireless cards. E.g. in VirtualBox, you then have to choose the *"MT SERVER"* model of the NIC.

Lastly, mount the `tpotce.iso` ISO to the VM and continue with the installation.<br>

You can now jump [here](#firstrun).

<a name="hardware"></a>
## Running on Hardware
If you decide to run T-Pot on dedicated hardware, just follow these steps:

1. Burn a CD from the ISO image or make a bootable USB stick using the image. <br>
Whereas most CD burning tools allow you to burn from ISO images, the procedure to create a bootable USB stick from an ISO image depends on your system. There are various Windows GUI tools available, e.g. [this tip](http://www.ubuntu.com/download/desktop/create-a-usb-stick-on-windows) might help you.<br> On [Linux](http://askubuntu.com/questions/59551/how-to-burn-a-iso-to-a-usb-device) or [MacOS](http://www.ubuntu.com/download/desktop/create-a-usb-stick-on-mac-osx) you can use the tool *dd* or create the USB stick with T-Pot's [ISO Creator](https://github.com/dtag-dev-sec).
2. Boot from the USB stick and install.

*Please note*: We will ensure the compatibility with the Intel NUC platform, as we really like the form factor, looks and build quality.

<a name="firstrun"></a>
## First Run
The installation requires very little interaction, only some locales and keyboard settings have to be answered. Everything else will be configured automatically. The system will reboot two times. Make sure it can access the internet as it needs to download the updates and the dockerized honeypot components. Depending on your network connection and the chosen installation type, the installation may take some time. During our tests (50Mbit down, 10Mbit up), the installation was usually finished within <=30 minutes.

Once the installation is finished, the system will automatically reboot and you will be presented with the T-Pot login screen. The user credentials for the first login are:

- user: *tsec*
- pass: *tsec*

You will need to set a new password after first login.

All honeypot services are started automatically.

<a name="placement"></a>
# System Placement
Make sure your system is reachable through the internet. Otherwise it will not capture any attacks, other than the ones from your hostile internal network! We recommend you put it in an unfiltered zone, where all TCP and UDP traffic is forwarded to T-Pot's network interface.

If you are behind a NAT gateway (e.g. home router), here is a list of ports that should be forwarded to T-Pot.

| Honeypot|Transport|Forwarded ports|
|---|---|---|
| conpot | TCP | 81, 102, 502 |
| conpot | UDP | 161 |
| cowrie | TCP | 22 |
| dionaea | TCP  | 21, 42, 135, 443, 445, 1433, 3306, 5060, 5061, 8081  |
| dionaea | UDP  | 69, 5060 |  
| elasticpot | TCP | 9200 |
| emobility | TCP | 8080 |
| glastopf | TCP | 80   |
| honeytrap | TCP | 25, 110, 139, 3389, 4444, 4899, 5900, 21000 |

<br>

Basically, you can forward as many TCP ports as you want, as honeytrap dynamically binds any TCP port that is not covered by the other honeypot daemons.

In case you need external SSH access, forward TCP port 64295 to T-Pot, see below.

T-Pot requires outgoing http and https connections for updates (ubuntu, docker) and attack submission (ewsposter, hpfeeds).


<a name="options"></a>
# Options
The system is designed to run without any interaction or maintenance and automatically contribute to the community.<br>
We know, for some this may not be enough. So here come some ways to further inspect the system and change configuration parameters.

<a name="ssh"></a>
## Enabling 2FA & SSH
By default, the SSH daemon is disabled. However, if you want to be able to login remotely via SSH and / or enable two-factor authentication (2fa) by using an authenticator app i.e. [Google Authenticator](https://support.google.com/accounts/answer/1066447?hl=en) just run the following script as the user *tsec*. ***Do not run it as root or via sudo***. Otherwise the setup of the two factor authentication will be bound to the user root who is not permitted to login remotely.

    ~/2fa_enable.sh

Afterwards you can login via SSH using the password you set for the user *tsec* and use the authenticator token as the second authentication factor.

The script will also enable the SSH daemon on **tcp/64295**. It is configured to prevent password login and use pubkey-authentication or challenge-response instead. We recommend using pubkey-authentication; just copy your SSH keyfile to `/home/tsec/.ssh/authorized_keys` and set the appropriate permissions (`chmod 600 authorized_keys`) as well as the correct ownership (`chown tsec:tsec authorized_keys`).


<a name="kibana"></a>
## Kibana Dashboard
To access the kibana dashboard, ensure you have [enabled SSH](#ssh) on T-Pot. If you have you can use [SSH port forwarding](http://explainshell.com/explain?cmd=ssh+-p+64295+-l+tsec+-N+-L8080%3A127.0.0.1%3A64296+yourHoneypotIPaddress)  to access the kibana dashboard (make sure you leave the terminal open).

    ssh -p 64295 -l tsec -N -L8080:127.0.0.1:64296 <yourHoneypotIPaddress>

Finally, open a web browser and access [http://127.0.0.1:8080](http://127.0.0.1:8080). The kibana dashboard can be customized to fit your needs. By default, we haven't added any filtering, because the filters depend on your setup. E.g. you might want to filter out your incoming administrative ssh connections and connections to update servers.

![Dashbaord](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/dashboard.png)

<a name="maintenance"></a>
## Maintenance
As mentioned before, the system was designed to be low maintenance. Basically, there is nothing you have to do but let it run. If one of the dockerized daemon fails, it will restart. If this fails, the regarding upstart job will be restarted.

If you run into any problems, a reboot may fix it. ;)

If new versions of the components involved appear, we will test them and build new docker images. Those new docker images will be pushed to docker hub and downloaded to T-Pot and activated accordingly.  

<a name="submission"></a>
## Community Data Submission
We provide T-Pot in order to make it accessible to all parties interested in honeypot deployment. By default, the data captured is submitted to a community backend. This community backend uses the data to feed a [community data view](http://sicherheitstacho.eu/?peers=communityPeers), a separate channel on our own [Sicherheitstacho](http://sicherheitstacho.eu), which is powered by our own set of honeypots.
You may opt out the submission to our community server by disabling it in the `[EWS]`-section of the config file `/data/ews/conf/ews.cfg`.

Further we support [hpfeeds](https://github.com/rep/hpfeeds). It is disabled by default since you need to supply a channel you want to post to and enter your user credentials. To enable hpfeeds, edit the config file `/data/ews/conf/ews.cfg`, section `[HPFEED]` and set it to true.

Data is submitted in a structured ews-format, a XML stucture. Hence, you can parse out the information that is relevant to you.

We encourage you not to disable the data submission as it is the main purpose of the community approach - as you all know **sharing is caring** 😍

The *`/data/ews/conf/ews.cfg`* file contains many configuration parameters required for the system to run. You can - if you want - add an email address, that will be included with your submissions, in order to be able to identify your requests later. Further you can add a proxy.
Please do not change anything other than those settings and only if you absolutely need to. Otherwise, the system may not work as expected.

<a name="roadmap"></a>
# Roadmap
As with every development there is always room for improvements ...

- Move to Ubuntu Server 16.04 LTS
- Further improve on JSON logging
- Move from upstart to systemd (only if necessary)
- Bump ELK-stack to 5.0
- Move from Glastopf to SNARE
- Work on a upgrade strategy
- Improve backup script, include restore script
- Tweaking 😎

Some features may be provided with updated docker images, others may require some hands on from your side.

You are always invited to participate in development on our [GitHub](https://github.com/dtag-dev-sec/tpotce) page.

<a name="disclaimer"></a>
# Disclaimer
- We don't have access to your system. So we cannot remote-assist when you break your configuration. But you can simply reinstall.
- The software was designed with best effort security, not to be in stealth mode. Because then, we probably would not be able to provide those kind of honeypot services.
- You install and you run within your responsibility. Choose your deployment wisely as a system compromise can never be ruled out.
- Honeypots should - by design - not host any sensitive data. Make sure you don't add any.
- By default, your data is submitted to the community dashboard. You can disable this in the config. But hey, wouldn't it be better to contribute to the community?
- By default, hpfeeds submission is disabled. You can enable it in the config section for hpfeeds. This is due to the nature of hpfeeds. We do not want to spam any channel, so you can choose where to post your data and who to share it with.  
- Malware submission is enabled by default but malware is currently not processed on the submission backend. This may be added later, but can also be disabled in the `ews.cfg` config file.
- The system restarts the docker containers every night to avoid clutter and reduce disk consumption. *All data in the container is then reset.* The data displayed in kibana is kept for <=90 days. 


<a name="faq"></a>
# FAQ
Please report any issues or questions on our [GitHub issue list](https://github.com/dtag-dev-sec/tpotce/issues), so the community can participate.

<a name="contact"></a>
# Contact
We provide the software **as is** in a Community Edition format. T-Pot is designed to run out of the box and with zero maintenance involved. <br>
We hope you understand that we cannot provide support on an individual basis. We will try to address questions, bugs and problems on our [GitHub issue list](https://github.com/dtag-dev-sec/tpotce/issues).

For general feedback you can write to cert @ telekom.de.

<a name="licenses"></a>
# Licenses
The software that T-Pot is built on, uses the following licenses.
<br>GPLv2: [conpot (by Lukas Rist)](https://github.com/mushorg/conpot/blob/master/LICENSE.txt), [dionaea](https://github.com/DinoTools/dionaea/blob/master/LICENSE), [honeytrap (by Tillmann Werner)](https://github.com/armedpot/honeytrap/blob/master/LICENSE), [suricata](http://suricata-ids.org/about/open-source/)
<br>GPLv3: [elasticpot (by Markus Schmall)](https://github.com/schmalle/ElasticPot), [emobility (by Mohamad Sbeiti)](https://github.com/dtag-dev-sec/emobility/blob/master/LICENSE), [ewsposter (by Markus Schroer)](https://github.com/dtag-dev-sec/ews/), [glastopf (by Lukas Rist)](https://github.com/glastopf/glastopf/blob/master/GPL)
<br>Apache 2 License: [elasticsearch](https://github.com/elasticsearch/elasticsearch/blob/master/LICENSE.txt), [logstash](https://github.com/elasticsearch/logstash/blob/master/LICENSE), [kibana](https://github.com/elasticsearch/kibana/blob/master/LICENSE.md), [docker] (https://github.com/docker/docker/blob/master/LICENSE)
<br>MIT License: [tagcloud (by Shelby Sturgis)](https://github.com/stormpython/tagcloud/blob/master/LICENSE), [heatmap (by Shelby Sturgis)](https://github.com/stormpython/heatmap/blob/master/LICENSE)
<br>[cowrie (copyright disclaimer by Upi Tamminen)](https://github.com/micheloosterhof/cowrie/blob/master/doc/COPYRIGHT)
<br>[Ubuntu licensing](http://www.ubuntu.com/about/about-ubuntu/licensing)

<a name="credits"></a>
# Credits
Without open source and the fruitful development community we are proud to be a part of T-Pot would not have been possible. Our thanks are extended but not limited to the following people and organizations:

###The developers and development communities of

* [conpot](https://github.com/mushorg/conpot/graphs/contributors)
* [cowrie](https://github.com/micheloosterhof/cowrie/graphs/contributors)
* [dionaea](https://github.com/DinoTools/dionaea/graphs/contributors)
* [docker](https://github.com/docker/docker/graphs/contributors)
* [elasticpot](https://github.com/schmalle/ElasticPot/graphs/contributors)
* [elasticsearch](https://github.com/elastic/elasticsearch/graphs/contributors)
* [emobility](https://github.com/dtag-dev-sec/emobility/graphs/contributors)
* [ewsposter](https://github.com/armedpot/ewsposter/graphs/contributors)
* [glastopf](https://github.com/mushorg/glastopf/graphs/contributors)
* [heatmap](https://github.com/stormpython/heatmap/graphs/contributors)
* [honeytrap](https://github.com/armedpot/honeytrap/graphs/contributors)
* [kibana](https://github.com/elastic/kibana/graphs/contributors)
* [logstash](https://github.com/elastic/logstash/graphs/contributors)
* [p0f](http://lcamtuf.coredump.cx/p0f3/)
* [suricata](https://github.com/inliniac/suricata/graphs/contributors)
* [tagcloud](https://github.com/stormpython/tagcloud/graphs/contributors)
* [ubuntu](http://www.ubuntu.com/)

###The following companies and organizations
* [cannonical](http://www.canonical.com/)
* [docker](https://www.docker.com/)
* [elastic.io](https://www.elastic.co/)
* [honeynet project](https://www.honeynet.org/)
* [intel](http://www.intel.de/content/www/de/de/homepage.html)

### ... and of course ***you*** for joining the community!


<a name="staytuned"></a>
# Stay tuned ...
We will be releasing a new version of T-Pot about every 6 months.

<a name="funfact"></a>
# Fun Fact

Coffee just does not cut it anymore which is why we needed a different caffeine source and consumed *203* bottles of [Club Mate](https://de.wikipedia.org/wiki/Club-Mate) during the development of T-Pot 16.03 😇
