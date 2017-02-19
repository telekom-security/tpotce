# T-Pot 16.10 Image Creator

This repository contains the necessary files to create the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)**  ISO image.
The image can then be used to install T-Pot on a physical or virtual machine.

In March 2016 we released
[T-Pot 16.03](http://dtag-dev-sec.github.io/mediator/feature/2016/03/11/t-pot-16.03.html)

# T-Pot 16.10

T-Pot 16.10 now uses Ubuntu Server 16.04 LTS and is based on

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

* [ELK stack](https://www.elastic.co/videos) to beautifully visualize all the events captured by T-Pot.
* [Elasticsearch Head](https://mobz.github.io/elasticsearch-head/) a web front end for browsing and interacting with an Elastic Search cluster.
* [Netdata](http://my-netdata.io/) for real-time performance monitoring.
* [Portainer](http://portainer.io/) a web based UI for docker.
* [Suricata](http://suricata-ids.org/) a Network Security Monitoring engine.
* [Wetty](https://github.com/krishnasrinivas/wetty) a web based SSH client.



# TL;DR
1. Meet the [system requirements](#requirements). The T-Pot installation needs at least 4 GB RAM and 64 GB free disk space as well as a working internet connection.
2. Download the [tpot.iso](http://community-honeypot.de/tpot.iso) or [create it yourself](#createiso).
3. Install the system in a [VM](#vm) or on [physical hardware](#hw) with [internet access](#placement).
4. Enjoy your favorite beverage - [watch](http://sicherheitstacho.eu/?peers=communityPeers) and [analyze](#kibana).

Seeing is believing :bowtie:

[![T-Pot 16.10 - Webified](https://img.youtube.com/vi/SNo7CkQ7ZWQ/0.jpg)](https://www.youtube.com/watch?v=SNo7CkQ7ZWQ)


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
  - [SSH and web access](#ssh)
  - [Kibana Dashboard](#kibana)
  - [Tools](#tools)
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
- **Ubuntu 16.04 LTS** is now being used as T-Pot's OS base
- **Size does matter** 😅
	- `tpot.iso` is now based on **Ubuntu's** network installer reducing the image download size by 600MB from 650MB to only **50MB**
	- All docker images have been rebuilt to reduce the image size at least by 50MB in some cases even 400-600MB
	- A "Everything" installation takes roughly 2GB less download size (counting from initial image download)
- **Introducing** new tools making things a lot easier for new users
	- [Elasticsearch Head](https://mobz.github.io/elasticsearch-head/) a web front end for browsing and interacting with an Elastic Search cluster.
	- [Netdata](http://my-netdata.io/) for real-time performance monitoring.
	- [Portainer](http://portainer.io/) a web based UI for docker.
	- [Wetty](https://github.com/krishnasrinivas/wetty) a web based SSH client.
- **NGINX** implemented as HTTPS reverse proxy
	- Access Kibana, ES Head plugin, UI-for-Docker, WebSSH and Netdata via browser!
	- Two factor based SSH tunnel is no longer needed!
- **Installation** procedure improved
	- Set your own password for the *tsec* user
	- Choose your installation type without the need of building your own image
	- Setup a remote user / password for secure web access including a self-signed-certificate
	- Easy to remember hostnames
- **First login** easy and secure
	- Access from console, ssh or web
	- No two-factor-authentication needed for ssh when logging in from RFC1918 networks
	- Enforcing public-key authentication for ssh connections other than RFC1918 networks
- **Systemd** now supersedes *upstart* as init system. All upstart scripts were ported to systemd along with the following improvements:
	- Improved start / stop handling of containers
	- Set persistence individually per container startup scripts (`/etc/systemd/system`)
	- Set persistence globally (`/usr/bin/clean.sh`)
- **Honeypot updates and improvements**
	- **Conpot** now supports **JSON logging** with many thanks as to making this feature request possible going to: 
		- [Andrea Pasquale](https://github.com/adepasquale),
		- [Danilo Massa](https://github.com/danilo-massa) &
		- [Johnny Vestergaard](https://github.com/johnnykv) 
	- **Cowrie** is now supporting **telnet** which is highly appreciated and thank you
		- [Michel Oosterhof](https://github.com/micheloosterhof)
	- **Dionaea** now supports **JSON logging** with many thanks as to making this feature request possible going to:
		- [PhiBo](https://github.com/phibos)
	- **Elasticpot** now supports **logging all queries and requests** with many thanks as to making this feature request possible going to:
		- [Markus Schmall](https://github.com/schmalle)
	- **Honeytrap** now supports **JSON logging** with many thanks as to making this feature request possible going to: 
		- [Andrea Pasquale](https://github.com/adepasquale)
- **Updates**
	- **Docker** was updated to the latest **1.12.2** release
	- **ELK** was updated to the latest **Kibana 4.6.2**, **Elasticsearch 2.4.1** and **Logstash 2.4.0** releases.
	- **Suricata** was updated to the latest **3.1.2** version including the latest **Emerging Threats** community ruleset.
- We now have **150 Visualizations** pre-configured and compiled to 14 individual **Kibana Dashboards** for every honeypot. Monitor all *honeypot events* locally on your T-Pot installation. Aside from *honeypot events* you can also view *Suricata NSM, Syslog and NGINX* events for a quick overview of local host events.
- More **Smart links** are now included.

<a name="concept"></a>
# Technical Concept

T-Pot is based on the network installer of Ubuntu Server 16.04 LTS.
The honeypot daemons as well as other support components being used have been paravirtualized using [docker](http://docker.io).
This allows us to run multiple honeypot daemons on the same network interface without problems and thus making the entire system very low maintenance. <br>The encapsulation of the honeypot daemons in docker provides a good isolation of the runtime environments and easy update mechanisms.

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
Important log data is now also stored outside the container in `/data/<container-name>` allowing easy access to logs from within the host and. The **systemd** scripts have been adjusted to support storing data on the host either volatile (*default*) or persistent (adjust individual systemd scripts in `/etc/systemd/system` or use a global setting in `/usr/bin/clear.sh`).

Basically, what happens when the system is booted up is the following:

- start host system
- start all the necessary services (i.e. docker-engine, reverse proxy, etc.)
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
- [netdata](https://github.com/dtag-dev-sec/netdata)
- [portainer](https://github.com/dtag-dev-sec/ui-for-docker)
- [suricata](https://github.com/dtag-dev-sec/suricata)

<a name="requirements"></a>
# System Requirements
Depending on your installation type, whether you install on [real hardware](#hardware) or in a [virtual machine](#vm), make sure your designated T-Pot system meets the following requirements:

##### T-Pot Installation (Cowrie, Dionaea, ElasticPot, Glastopf, Honeytrap, ELK, Suricata+P0f & Tools)
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:

- 4 GB RAM (6-8 GB recommended)
- 64 GB disk (128 GB SSD recommended)
- Network via DHCP
- A working internet connection

##### Sensor Installation (Cowrie, Dionaea, ElasticPot, Glastopf, Honeytrap)
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:

- 3 GB RAM (4-6 GB recommended)
- 64 GB disk (64 GB SSD recommended)
- Network via DHCP
- A working internet connection

##### Industrial Installation (ConPot, eMobility, ELK, Suricata+P0f & Tools)
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:

- 4 GB RAM (8 GB recommended)
- 64 GB disk (128 GB SSD recommended)
- Network via DHCP
- A working internet connection

##### Everything Installation (Everything, all of the above)
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:

- 8 GB RAM
- 128 GB disk or larger (128 GB SSD or larger recommended)
- Network via DHCP
- A working internet connection

<a name="installation"></a>
# Installation
The installation of T-Pot is straight forward. Please be advised that you should have an internet connection up and running as all all the docker images for the chosen installation type need to be pulled from docker hub.

Firstly, decide if you want to download our prebuilt installation ISO image [tpot.iso](http://community-honeypot.de/tpot.iso) ***or*** [create it yourself](#createiso).

Secondly, decide where you want to let the system run: [real hardware](#hardware) or in a [virtual machine](#vm)?

<a name="prebuilt"></a>
## Prebuilt ISO Image
We provide an installation ISO image for download (~50MB), which is created using the same [tool](https://github.com/dtag-dev-sec/tpotce) you can use yourself in order to create your own image. It will basically just save you some time downloading components and creating the ISO image.
You can download the prebuilt installation image [here](http://community-honeypot.de/tpot.iso) and jump to the [installation](#vm) section. The ISO image is hosted by our friends from [Strato](http://www.strato.de) / [Cronon](http://www.cronon.de).

    sha256sum tpot.iso
    df6b1db24d0dcc421125dc973fbb2d17aa91cd9ff94607dde9d1b09a92bcbaf0 tpot.iso

<a name="createiso"></a>
## Create your own ISO Image
For transparency reasons and to give you the ability to customize your install, we provide you the [ISO Creator](https://github.com/dtag-dev-sec/tpotce) that enables you to create your own ISO installation image.

**Requirements to create the ISO image:**
- Ubuntu 16.04 LTS or newer as host system (others *may* work, but remain untested)
- 4GB of free memory  
- 32GB of free storage
- A working internet connection

**How to create the ISO image:**

1. Clone the repository and enter it.

        git clone https://github.com/dtag-dev-sec/tpotce.git
        cd tpotce

2. Invoke the script that builds the ISO image.
The script will download and install dependencies necessary to build the image on the invoking machine. It will further download the ubuntu network installer image (~50MB) which T-Pot is based on.

        sudo ./makeiso.sh

After a successful build, you will find the ISO image `tpot.iso` along with a SHA256 checksum `tpot.sha256`in your directory.

<a name="vm"></a>
## Running in VM
You may want to run T-Pot in a virtualized environment. The virtual system configuration depends on your virtualization provider.

We successfully tested T-Pot with [VirtualBox](https://www.virtualbox.org) and [VMWare](http://www.vmware.com) with just little modifications to the default machine configurations.

It is important to make sure you meet the [system requirements](#requirements) and assign a virtual harddisk >=64 GB, >=4 GB RAM and bridged networking to T-Pot.

You need to enable promiscuous mode for the network interface for suricata to work properly. Make sure you enable it during configuration.

If you want to use a wifi card as primary NIC for T-Pot, please remind that not all network interface drivers support all wireless cards. E.g. in VirtualBox, you then have to choose the *"MT SERVER"* model of the NIC.

Lastly, mount the `tpot.iso` ISO to the VM and continue with the installation.<br>

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
The installation requires very little interaction, only some locales and keyboard settings have to be answered. Everything else will be configured automatically. The system will reboot two times. Make sure it can access the internet as it needs to download the updates and the dockerized honeypot components. Depending on your network connection and the chosen installation type, the installation may take some time. During our tests (50Mbit down, 10Mbit up), the installation is usually finished within <=30 minutes.

Once the installation is finished, the system will automatically reboot and you will be presented with the T-Pot login screen. The user credentials for the first login are:

- user: **tsec**
- pass: **password you chose during the installation**

All honeypot services are preconfigured and are starting automatically.

You can also login from your browser: ``https://<your.ip>:64297``

- user: **user you chose during the installation**
- pass: **password you chose during the installation**


<a name="placement"></a>
# System Placement
Make sure your system is reachable through the internet. Otherwise it will not capture any attacks, other than the ones from your hostile internal network! We recommend you put it in an unfiltered zone, where all TCP and UDP traffic is forwarded to T-Pot's network interface.

If you are behind a NAT gateway (e.g. home router), here is a list of ports that should be forwarded to T-Pot.

| Honeypot|Transport|Forwarded ports|
|---|---|---|
| conpot | TCP | 1025, 50100 |
| cowrie | TCP | 22, 23 |
| dionaea | TCP  | 21, 42, 135, 443, 445, 1433, 1723, 1883, 1900, 3306, 5060, 5061, 8081, 11211  |
| dionaea | UDP  | 69, 5060 |  
| elasticpot | TCP | 9200 |
| emobility | TCP | 8080 |
| glastopf | TCP | 80   |
| honeytrap | TCP | 25, 110, 139, 3389, 4444, 4899, 5900, 21000 |

<br>

Basically, you can forward as many TCP ports as you want, as honeytrap dynamically binds any TCP port that is not covered by the other honeypot daemons.

In case you need external SSH access, forward TCP port 64295 to T-Pot, see below.
In case you need external web access, forward TCP port 64297 to T-Pot, see below.

T-Pot requires outgoing http and https connections for updates (ubuntu, docker) and attack submission (ewsposter, hpfeeds).


<a name="options"></a>
# Options
The system is designed to run without any interaction or maintenance and automatically contribute to the community.<br>
We know, for some this may not be enough. So here come some ways to further inspect the system and change configuration parameters.

<a name="ssh"></a>
## SSH and web access
By default, the SSH daemon only allows access on **tcp/64295** with a user / password combination from RFC1918 networks. However, if you want to be able to login remotely via SSH you need to put your SSH keys on the host as described below.<br>
It is configured to prevent password login from official IP addresses and pubkey-authentication must be used. Copy your SSH keyfile to `/home/tsec/.ssh/authorized_keys` and set the appropriate permissions (`chmod 600 authorized_keys`) as well as the correct ownership (`chown tsec:tsec authorized_keys`).

If you do not have a SSH client at hand and still want to access the machine via SSH you can do so by directing your browser to `https://<your.ip>:64297`, enter

- user: **user you chose during the installation**
- pass: **password you chose during the installation**

and choose **WebSSH** from the navigation bar. You will be prompted to allow access for this connection and enter the password for the user **tsec**.

![WebSSH](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/webssh.png)

<a name="kibana"></a>
## Kibana Dashboard
Just open a web browser and access and connect to `https://<your.ip>:64297`, enter

- user: **user you chose during the installation**
- pass: **password you chose during the installation**

and the **Kibana dashboard** will automagically load. The Kibana dashboard can be customized to fit your needs. By default, we haven't added any filtering, because the filters depend on your setup. E.g. you might want to filter out your incoming administrative ssh connections and connections to update servers.

![Dashbaord](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/dashboard.png)

<a name="tools"></a>
## Tools
We included some web based management tools to improve and ease up on your daily tasks.

![ES Head Plugin](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/headplugin.png)
![UI-For-Docker](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/dockerui.png)
![Netdata](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/netdata.png)

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

- Bump ELK-stack to 5.0
- Move from Glastopf to SNARE
- Documentation 😎

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
<br>GPLv3: [elasticpot (by Markus Schmall)](https://github.com/schmalle/ElasticPot), [emobility (by Mohamad Sbeiti)](https://github.com/dtag-dev-sec/emobility/blob/master/LICENSE), [ewsposter (by Markus Schroer)](https://github.com/dtag-dev-sec/ews/), [glastopf (by Lukas Rist)](https://github.com/glastopf/glastopf/blob/master/GPL), [netdata](https://github.com/firehol/netdata/blob/master/LICENSE.md)
<br>Apache 2 License: [elasticsearch](https://github.com/elasticsearch/elasticsearch/blob/master/LICENSE.txt), [logstash](https://github.com/elasticsearch/logstash/blob/master/LICENSE), [kibana](https://github.com/elasticsearch/kibana/blob/master/LICENSE.md), [docker] (https://github.com/docker/docker/blob/master/LICENSE), [elasticsearch-head](https://github.com/mobz/elasticsearch-head/blob/master/LICENCE)
<br>MIT License: [tagcloud (by Shelby Sturgis)](https://github.com/stormpython/tagcloud/blob/master/LICENSE), [heatmap (by Shelby Sturgis)](https://github.com/stormpython/heatmap/blob/master/LICENSE), [wetty](https://github.com/krishnasrinivas/wetty/blob/master/LICENSE)
<br>[cowrie (copyright disclaimer by Upi Tamminen)](https://github.com/micheloosterhof/cowrie/blob/master/doc/COPYRIGHT)
<br>[Ubuntu licensing](http://www.ubuntu.com/about/about-ubuntu/licensing)
<br>[Portainer](https://github.com/portainer/portainer/blob/develop/LICENSE)

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
* [elasticsearch-head](https://github.com/mobz/elasticsearch-head/graphs/contributors)
* [emobility](https://github.com/dtag-dev-sec/emobility/graphs/contributors)
* [ewsposter](https://github.com/armedpot/ewsposter/graphs/contributors)
* [glastopf](https://github.com/mushorg/glastopf/graphs/contributors)
* [heatmap](https://github.com/stormpython/heatmap/graphs/contributors)
* [honeytrap](https://github.com/armedpot/honeytrap/graphs/contributors)
* [kibana](https://github.com/elastic/kibana/graphs/contributors)
* [logstash](https://github.com/elastic/logstash/graphs/contributors)
* [netdata](https://github.com/firehol/netdata/graphs/contributors)
* [p0f](http://lcamtuf.coredump.cx/p0f3/)
* [portainer](https://github.com/portainer/portainer/graphs/contributors)
* [suricata](https://github.com/inliniac/suricata/graphs/contributors)
* [tagcloud](https://github.com/stormpython/tagcloud/graphs/contributors)
* [ubuntu](http://www.ubuntu.com/)
* [wetty](https://github.com/krishnasrinivas/wetty/graphs/contributors)

###The following companies and organizations
* [cannonical](http://www.canonical.com/)
* [docker](https://www.docker.com/)
* [elastic.io](https://www.elastic.co/)
* [honeynet project](https://www.honeynet.org/)
* [intel](http://www.intel.com)

### ... and of course ***you*** for joining the community!


<a name="staytuned"></a>
# Stay tuned ...
We will be releasing a new version of T-Pot about every 6 months.

<a name="funfact"></a>
# Fun Fact

Coffee just does not cut it anymore which is why we needed a different caffeine source and consumed *107* bottles of [Club Mate](https://de.wikipedia.org/wiki/Club-Mate) during the development of T-Pot 16.10 😇
