# T-Pot 17.10

This repository contains the necessary files to create the **[T-Pot](https://github.com/dtag-dev-sec/tpotce/releases)** ISO image.
The image can then be used to install T-Pot on a physical or virtual machine.

In October 2016 we released
[T-Pot 16.10](http://dtag-dev-sec.github.io/mediator/feature/2016/10/31/t-pot-16.10.html)

# T-Pot 17.10

T-Pot 17.10 runs on the latest 16.04 LTS Ubuntu Server Network Installer image, is based on

[docker](https://www.docker.com/), [docker-compose](https://docs.docker.com/compose/)

and includes dockerized versions of the following honeypots

* [conpot](http://conpot.org/),
* [cowrie](http://www.micheloosterhof.com/cowrie/),
* [dionaea](https://github.com/DinoTools/dionaea),
* [elasticpot](https://github.com/schmalle/ElasticPot),
* [emobility](https://github.com/dtag-dev-sec/emobility),
* [glastopf](http://glastopf.org/),
* [honeytrap](https://github.com/armedpot/honeytrap/),
* [mailoney](https://github.com/awhitehatter/mailoney),
* [rdpy](https://github.com/citronneur/rdpy) and
* [vnclowpot](https://github.com/magisterquis/vnclowpot)


Furthermore we use the following tools

* [ELK stack](https://www.elastic.co/videos) to beautifully visualize all the events captured by T-Pot.
* [Elasticsearch Head](https://mobz.github.io/elasticsearch-head/) a web front end for browsing and interacting with an Elastic Search cluster.
* [Netdata](http://my-netdata.io/) for real-time performance monitoring.
* [Portainer](http://portainer.io/) a web based UI for docker.
* [Spiderfoot](https://github.com/smicallef/spiderfoot) a open source intelligence automation tool.
* [Suricata](http://suricata-ids.org/) a Network Security Monitoring engine.
* [Wetty](https://github.com/krishnasrinivas/wetty) a web based SSH client.



# TL;DR
1. Meet the [system requirements](#requirements). The T-Pot installation needs at least 4 GB RAM and 64 GB free disk space as well as a working internet connection.
2. Download the T-Pot ISO from [GitHub](https://github.com/dtag-dev-sec/tpotce/releases) or [create it yourself](#createiso).
3. Install the system in a [VM](#vm) or on [physical hardware](#hw) with [internet access](#placement).
4. Enjoy your favorite beverage - [watch](http://sicherheitstacho.eu/?peers=communityPeers) and [analyze](#kibana).

# T-Pot-Autoinstaller
T-Pot may also be installed on an existing machine using the [T-Pot-Autoinstaller](https://github.com/dtag-dev-sec/t-pot-autoinstall).

# Seeing is believing :bowtie:

[![T-Pot 17.10](https://img.youtube.com/vi/G-_OabDowFU/0.jpg)](https://youtu.be/G-_OabDowFU)


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
- **Size still matters** 😅
	- All docker images have been rebuilt as micro containers based on Alpine Linux to even further reduce the image size and leading to image sizes (compressed) below the 50 MB mark. The uncompressed size of eMobility and the ELK stack could each be reduced by a whopping 600 MB!
	- A "Everything" installation now takes roughly 1.6 GB download size
- **docker-compose**
	- T-Pot containers are now being controlled and monitored through docker-compose and a single configuration file `/opt/tpot/etc/tpot.yml` allowing for greater flexibility and resulting in easier image management (i.e. updated images).
	- As a benefit only a single `systemd` script `/etc/systemd/system/tpot.service` is needed to start `systemctl start tpot` and stop `systemctl stop tpot` the T-Pot services.
	- There are four pre-configured compose configurations which do reflect the T-Pot editions `/opt/tpot/etc/compose`. Simply stop the T-Pot services and copy i.e. `cp /opt/tpot/etc/compose/all.yml /opt/tpot/etc/tpot.yml`, restart the T-Pot services and the selcted edition will be running after downloading the required docker images.
- **Introducing** [Spiderfoot](https://github.com/smicallef/spiderfoot) a open source intelligence automation tool.
- **Installation** procedure simplified
	- Within the Ubuntu Installer you only have to choose language settings
	- After the first reboot the T-Pot installer checks if internet and required services are reachable before the installation procedure begins
	- T-Pot Installer now uses a “dialog” which looks way better than the old text based installer
	- `tsec` user & password dialog is now part of the T-Pot Installer
	- The self-signed certificate is now created automatically to reduce unnecessary overhead for novice users
	- New ASCII logo and login screen pointing to web and ssh logins
	- Hostnames are now generated using an offline name generator, which still produces funny and collision free hostnames
- **CVE IDs for Suricata**
	- Our very own [Listbot](https://github.com/dtag-dev-sec/listbot) builds translation maps for Logstash. If Logstash registers a match the events' CVE ID will be stored alongside the event within Elasticsearch.
- **IP Reputations**
	- [Listbot](https://github.com/dtag-dev-sec/listbot) also builds translation maps for blacklisted IPs
	- Based upon 30+ publicly available IP blacklisting sources listbot creates a logstash translation map matching the events' source IP addresses against the IPs reputation
	- If the source IP is known to a blacklist service a corresponding tag will be stored with the event
	- Updates occur on every logstash container start; by default every 24h
- **Honeypot updates and improvements**
	- All honeypots were updated to their latest & stable versions.
- **New Honeypots** were added ...
	* [mailoney](https://github.com/awhitehatter/mailoney)
		- A low interaction SMTP honeypot
	* [rdpy](https://github.com/citronneur/rdpy)
		- A low interaction RDP honeypot
	* [vnclowpot](https://github.com/magisterquis/vnclowpot)
		- A low interaction VNC honeypot
- **Persistence** is now enabled by default and will keep honeypot logs and tools data in `/data/` and its sub-folders by default for 30 days. You may change that behavior in `/opt/tpot/etc/logrotate/logrotate.conf`. ELK data however will be kept for 90 days by default. You may change that behavior in `/opt/tpot/etc/curator/actions.yml`. Scripts will be triggered through `/etc/crontab`.
- **Updates**
	- **Docker** was updated to the latest **1.12.6** release within Ubuntu 16.04.x LTS
	- **ELK** was updated to the latest **Kibana 5.6.3**, **Elasticsearch 5.6.3** and **Logstash 5.6.3** releases.
	- **Suricata** was updated to the latest **4.0.0** version including the latest **Emerging Threats** community ruleset.
- **Dashboards Makeover**
	- We now have **160+ Visualizations** pre-configured and compiled to 14 individual **Kibana Dashboards** for every honeypot. Monitor all *honeypot events* locally on your T-Pot installation. Aside from *honeypot events* you can also view *Suricata NSM, Syslog and NGINX* events for a quick overview of local host events.
	- View available IP reputation of any source IP address
	- View available CVE ID for events
	- More **Smart links** are now included.
- **Update Feature**
	- For the ones who like to live on the bleeding edge of T-Pot development there is now a update script available in `/opt/tpot/update.sh`. Just run the script and it will get the latest changes from the `master branch`. For now this feature is experimental and the first step to a true rolling release cycle.
- **Files & Folders**
	- While the `/data` folder is still in its old place, all T-Pot relevant files and folders have been restructured and will now be installed into `/opt/tpot`. Only a few system relevant files with regard to the installed OS and its services will be copied to locations outside the T-Pot base path.

<a name="concept"></a>
# Technical Concept

T-Pot is based on the network installer of Ubuntu Server 16.04.x LTS.
The honeypot daemons as well as other support components being used have been containerized using [docker](http://docker.io).
This allows us to run multiple honeypot daemons on the same network interface while maintaining a small footprint and constrain each honeypot within its own environment.

In T-Pot we combine the dockerized honeypots
[conpot](http://conpot.org/),
[cowrie](http://www.micheloosterhof.com/cowrie/),
[dionaea](https://github.com/DinoTools/dionaea),
[elasticpot](https://github.com/schmalle/ElasticPot),
[emobility](https://github.com/dtag-dev-sec/emobility),
[glastopf](http://glastopf.org/),
[honeytrap](https://github.com/armedpot/honeytrap/),
[mailoney](https://github.com/awhitehatter/mailoney),
[rdpy](https://github.com/citronneur/rdpy) and
[vnclowpot](https://github.com/magisterquis/vnclowpot) with
[ELK stack](https://www.elastic.co/videos) to beautifully visualize all the events captured by T-Pot,
[Elasticsearch Head](https://mobz.github.io/elasticsearch-head/) a web front end for browsing and interacting with an Elastic Search cluster,
[Netdata](http://my-netdata.io/) for real-time performance monitoring,
[Portainer](http://portainer.io/) a web based UI for docker,
[Spiderfoot](https://github.com/smicallef/spiderfoot) a open source intelligence automation tool,
[Suricata](http://suricata-ids.org/) a Network Security Monitoring engine and
[Wetty](https://github.com/krishnasrinivas/wetty) a web based SSH client.

![Architecture](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/architecture.png)

While data within docker containers is volatile we do now ensure a default 30 day persistence of all relevant honeypot and tool data in the well known `/data` folder and sub-folders. The persistence configuration may be adjusted in `/opt/tpot/etc/logrotate/logrotate.conf`. Once a docker container crashes, all other data produced within its environment is erased and a fresh instance is started from the corresponding docker image.<br>

Basically, what happens when the system is booted up is the following:

- start host system
- start all the necessary services (i.e. docker-engine, reverse proxy, etc.)
- start all docker containers via docker-compose (honeypots, nms, elk)

Within the T-Pot project, we provide all the tools and documentation necessary to build your own honeypot system and contribute to our [community data view](http://sicherheitstacho.eu/?peers=communityPeers), a separate channel on our  [Sicherheitstacho](http://sicherheitstacho.eu) that is powered by T-Pot community data.

The source code and configuration files are stored in individual GitHub repositories, which are linked below. The docker images are pre-configured for the T-Pot environment. If you want to run the docker images separately, make sure you study the docker-compose configuration (`/opt/tpot/etc/tpot.yml`) and the T-Pot systemd script (`/etc/systemd/system/tpot.service`), as they provide a good starting point for implementing changes.

The individual docker configurations are located in the following GitHub repositories:

- [conpot](https://github.com/dtag-dev-sec/conpot)
- [cowrie](https://github.com/dtag-dev-sec/cowrie)
- [dionaea](https://github.com/dtag-dev-sec/dionaea)
- [elasticpot](https://github.com/dtag-dev-sec/elasticpot)
- [elk-stack](https://github.com/dtag-dev-sec/elk)
- [emobility](https://github.com/dtag-dev-sec/emobility)
- [ewsposter](https://github.com/dtag-dev-sec/ews)
- [glastopf](https://github.com/dtag-dev-sec/glastopf)
- [honeytrap](https://github.com/dtag-dev-sec/honeytrap)
- [mailoney](https://github.com/dtag-dev-sec/mailoney)
- [netdata](https://github.com/dtag-dev-sec/netdata)
- [portainer](https://github.com/dtag-dev-sec/ui-for-docker)
- [rdpy](https://github.com/dtag-dev-sec/rdpy)
- [spiderfoot](https://github.com/dtag-dev-sec/spiderfoot)
- [suricata & p0f](https://github.com/dtag-dev-sec/suricata)
- [vnclowpot](https://github.com/dtag-dev-sec/vnclowpot)

<a name="requirements"></a>
# System Requirements
Depending on your installation type, whether you install on [real hardware](#hardware) or in a [virtual machine](#vm), make sure your designated T-Pot system meets the following requirements:

##### T-Pot Installation (Cowrie, Dionaea, ElasticPot, Glastopf, Honeytrap, Mailoney, Rdpy, Vnclowpot, ELK, Suricata+P0f & Tools)
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:

- 4 GB RAM (6-8 GB recommended)
- 64 GB SSD (128 GB SSD recommended)
- Network via DHCP
- A working, non-proxied, internet connection

##### Honeypot Installation (Cowrie, Dionaea, ElasticPot, Glastopf, Honeytrap, Mailoney, Rdpy, Vnclowpot)
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:

- 3 GB RAM (4-6 GB recommended)
- 64 GB SSD (64 GB SSD recommended)
- Network via DHCP
- A working, non-proxied, internet connection

##### Industrial Installation (ConPot, eMobility, ELK, Suricata+P0f & Tools)
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:

- 4 GB RAM (8 GB recommended)
- 64 GB SSD (128 GB SSD recommended)
- Network via DHCP
- A working, non-proxied, internet connection

##### Everything Installation (Everything, all of the above)
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:

- 8+ GB RAM
- 128+ GB SSD
- Network via DHCP
- A working, non-proxied, internet connection

<a name="installation"></a>
# Installation
The installation of T-Pot is straight forward and heavily depends on a working, transparent and non-proxied up and running internet connection. Otherwise the installation **will fail!**

Firstly, decide if you want to download our prebuilt installation ISO image from [GitHub](https://github.com/dtag-dev-sec/tpotce/releases) ***or*** [create it yourself](#createiso).

Secondly, decide where you want to let the system run: [real hardware](#hardware) or in a [virtual machine](#vm)?

<a name="prebuilt"></a>
## Prebuilt ISO Image
We provide an installation ISO image for download (~50MB), which is created using the same [tool](https://github.com/dtag-dev-sec/tpotce) you can use yourself in order to create your own image. It will basically just save you some time downloading components and creating the ISO image.
You can download the prebuilt installation image from [GitHub](https://github.com/dtag-dev-sec/tpotce/releases) and jump to the [installation](#vm) section.

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
```
git clone https://github.com/dtag-dev-sec/tpotce
cd tpotce
```
2. Invoke the script that builds the ISO image.
The script will download and install dependencies necessary to build the image on the invoking machine. It will further download the ubuntu network installer image (~50MB) which T-Pot is based on.
```
sudo ./makeiso.sh
```
After a successful build, you will find the ISO image `tpot.iso` along with a SHA256 checksum `tpot.sha256` in your directory.

<a name="vm"></a>
## Running in VM
You may want to run T-Pot in a virtualized environment. The virtual system configuration depends on your virtualization provider.

We successfully tested T-Pot with [VirtualBox](https://www.virtualbox.org) and [VMWare](http://www.vmware.com) with just little modifications to the default machine configurations.

It is important to make sure you meet the [system requirements](#requirements) and assign a virtual harddisk >=64 GB, >=4 GB RAM and bridged networking to T-Pot.

You need to enable promiscuous mode for the network interface for suricata and p0f to work properly. Make sure you enable it during configuration.

If you want to use a wifi card as primary NIC for T-Pot, please be aware of the fact that not all network interface drivers support all wireless cards. E.g. in VirtualBox, you then have to choose the *"MT SERVER"* model of the NIC.

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
The installation requires very little interaction, only a locale and keyboard setting has to be answered for the basic linux installation. The system will reboot and please maintain an active internet connection. The T-Pot installer will start and ask you for an installation type, password for the **tsec** user and credentials for a **web user**. Everything else will be configured automatically. All docker images and other componenents will be downloaded. Depending on your network connection and the chosen installation type, the installation may take some time. During our tests (50Mbit down, 10Mbit up), the installation is usually finished within a 30 minute timeframe.

Once the installation is finished, the system will automatically reboot and you will be presented with the T-Pot login screen. On the console you may login with the **tsec** user:

- user: **tsec**
- pass: **password you chose during the installation**

All honeypot services are preconfigured and are starting automatically.

You can also login from your browser: ``https://<your.ip>:64297``

- user: **user you chose during the installation**
- pass: **password you chose during the installation**


<a name="placement"></a>
# System Placement
Make sure your system is reachable through the internet. Otherwise it will not capture any attacks, other than the ones from your  internal network! We recommend you put it in an unfiltered zone, where all TCP and UDP traffic is forwarded to T-Pot's network interface.

A list of all relevant ports is available as part of the [Technical Concept](#concept)
<br>

Basically, you can forward as many TCP ports as you want, as honeytrap dynamically binds any TCP port that is not covered by the other honeypot daemons.

In case you need external SSH access, forward TCP port 64295 to T-Pot, see below.
In case you need external web access, forward TCP port 64297 to T-Pot, see below.

T-Pot requires outgoing git, http, https connections for updates (Ubuntu, Docker, GitHub, PyPi) and attack submission (ewsposter, hpfeeds). Ports and availability may vary based on your geographical location.

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

and choose **WebTTY** from the navigation bar. You will be prompted to allow access for this connection and enter the password for the user **tsec**.

![WebTTY](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/webssh.png)

<a name="kibana"></a>
## Kibana Dashboard
Just open a web browser and access and connect to `https://<your.ip>:64297`, enter

- user: **user you chose during the installation**
- pass: **password you chose during the installation**

and **Kibana** will automagically load. The Kibana dashboard can be customized to fit your needs. By default, we haven't added any filtering, because the filters depend on your setup. E.g. you might want to filter out your incoming administrative ssh connections and connections to update servers.

![Dashbaord](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/dashboard.png)

<a name="tools"></a>
## Tools
We included some web based management tools to improve and ease up on your daily tasks.

![ES Head Plugin](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/headplugin.png)
![Netdata](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/netdata.png)
![Portainer](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/dockerui.png)
![Spiderfoot](https://raw.githubusercontent.com/dtag-dev-sec/tpotce/master/doc/spiderfoot.png)


<a name="maintenance"></a>
## Maintenance
As mentioned before, the system was designed to be low maintenance. Basically, there is nothing you have to do but let it run.

If you run into any problems, a reboot may fix it :bowtie:

If new versions of the components involved appear, we will test them and build new docker images. Those new docker images will be pushed to docker hub and downloaded to T-Pot and activated accordingly.  

<a name="submission"></a>
## Community Data Submission
We provide T-Pot in order to make it accessible to all parties interested in honeypot deployment. By default, the data captured is submitted to a community backend. This community backend uses the data to feed a [community data view](http://sicherheitstacho.eu/?peers=communityPeers), a separate channel on our own [Sicherheitstacho](http://sicherheitstacho.eu), which is powered by our own set of honeypots.
You may opt out the submission to our community server by removing the `# Ewsposter service` from `/opt/tpot/etc/tpot.yml`:
1. Stop T-Pot services: `systemctl stop tpot`
2. Remove Ewsposter service: `vi /opt/tpot/etc/tpot.yml`
3. Remove the following lines, save and exit vi (`:x!`):<br>
```
# Ewsposter service
  ewsposter:
    container_name: ewsposter
    restart: always
    networks:
     - ewsposter_local
    image: "dtagdevsec/ewsposter:1710"
    volumes:
     - /data:/data
     - /data/ews/conf/ews.ip:/opt/ewsposter/ews.ip
```
4. Start T-Pot services: `systemctl start tpot`

Data is submitted in a structured ews-format, a XML stucture. Hence, you can parse out the information that is relevant to you.

We encourage you not to disable the data submission as it is the main purpose of the community approach - as you all know **sharing is caring** 😍

<a name="roadmap"></a>
# Roadmap
As with every development there is always room for improvements ...

- Introduce new honeypots
- Improve automatic updates

Some features may be provided with updated docker images, others may require some hands on from your side.

You are always invited to participate in development on our [GitHub](https://github.com/dtag-dev-sec/tpotce) page.

<a name="disclaimer"></a>
# Disclaimer
- We don't have access to your system. So we cannot remote-assist when you break your configuration. But you can simply reinstall.
- The software was designed with best effort security, not to be in stealth mode. Because then, we probably would not be able to provide those kind of honeypot services.
- You install and you run within your responsibility. Choose your deployment wisely as a system compromise can never be ruled out.
- Honeypots should - by design - not host any sensitive data. Make sure you don't add any.
- By default, your data is submitted to the community dashboard. You can disable this in the config. But hey, wouldn't it be better to contribute to the community?

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
The software that T-Pot is built on uses the following licenses.
<br>GPLv2: [conpot (by Lukas Rist)](https://github.com/mushorg/conpot/blob/master/LICENSE.txt), [dionaea](https://github.com/DinoTools/dionaea/blob/master/LICENSE), [honeytrap (by Tillmann Werner)](https://github.com/armedpot/honeytrap/blob/master/LICENSE), [suricata](http://suricata-ids.org/about/open-source/)
<br>GPLv3: [elasticpot (by Markus Schmall)](https://github.com/schmalle/ElasticPot), [emobility (by Mohamad Sbeiti)](https://github.com/dtag-dev-sec/emobility/blob/master/LICENSE), [ewsposter (by Markus Schroer)](https://github.com/dtag-dev-sec/ews/), [glastopf (by Lukas Rist)](https://github.com/glastopf/glastopf/blob/master/GPL), [rdpy](https://github.com/citronneur/rdpy/blob/master/LICENSE), [netdata](https://github.com/firehol/netdata/blob/master/LICENSE.md)
<br>Apache 2 License: [elasticsearch](https://github.com/elasticsearch/elasticsearch/blob/master/LICENSE.txt), [logstash](https://github.com/elasticsearch/logstash/blob/master/LICENSE), [kibana](https://github.com/elasticsearch/kibana/blob/master/LICENSE.md), [docker](https://github.com/docker/docker/blob/master/LICENSE), [elasticsearch-head](https://github.com/mobz/elasticsearch-head/blob/master/LICENCE)
<br>MIT License: [ctop](https://github.com/bcicen/ctop/blob/master/LICENSE), [wetty](https://github.com/krishnasrinivas/wetty/blob/master/LICENSE)
<br>zlib License: [vnclowpot](https://github.com/magisterquis/vnclowpot/blob/master/LICENSE)
<br>[cowrie (copyright disclaimer by Upi Tamminen)](https://github.com/micheloosterhof/cowrie/blob/master/doc/COPYRIGHT)
<br>[mailoney](https://github.com/awhitehatter/mailoney)
<br>[Ubuntu licensing](http://www.ubuntu.com/about/about-ubuntu/licensing)
<br>[Portainer](https://github.com/portainer/portainer/blob/develop/LICENSE)

<a name="credits"></a>
# Credits
Without open source and the fruitful development community we are proud to be a part of, T-Pot would not have been possible! Our thanks are extended but not limited to the following people and organizations:

### The developers and development communities of

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
* [honeytrap](https://github.com/armedpot/honeytrap/graphs/contributors)
* [kibana](https://github.com/elastic/kibana/graphs/contributors)
* [logstash](https://github.com/elastic/logstash/graphs/contributors)
* [mailoney](https://github.com/awhitehatter/mailoney)
* [netdata](https://github.com/firehol/netdata/graphs/contributors)
* [p0f](http://lcamtuf.coredump.cx/p0f3/)
* [portainer](https://github.com/portainer/portainer/graphs/contributors)
* [rdpy](https://github.com/citronneur/rdpy)
* [spiderfoot](https://github.com/smicallef/spiderfoot)
* [suricata](https://github.com/inliniac/suricata/graphs/contributors)
* [ubuntu](http://www.ubuntu.com/)
* [vnclowpot](https://github.com/magisterquis/vnclowpot)
* [wetty](https://github.com/krishnasrinivas/wetty/graphs/contributors)

### The following companies and organizations
* [canonical](http://www.canonical.com/)
* [docker](https://www.docker.com/)
* [elastic.io](https://www.elastic.co/)
* [honeynet project](https://www.honeynet.org/)
* [intel](http://www.intel.com)

### ... and of course ***you*** for joining the community!

<a name="staytuned"></a>
# Stay tuned ...
We will be releasing a new version of T-Pot about every 6-12 months.

<a name="funfact"></a>
# Fun Fact

Coffee just does not cut it anymore which is why we needed a different caffeine source and consumed *242* bottles of [Club Mate](https://de.wikipedia.org/wiki/Club-Mate) during the development of T-Pot 17.10 😇
