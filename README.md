![T-Pot](doc/tpotsocial.png)

T-Pot 19.03 runs on Debian (Sid), is based heavily on

[docker](https://www.docker.com/), [docker-compose](https://docs.docker.com/compose/)

and includes dockerized versions of the following honeypots

* [adbhoney](https://github.com/huuck/ADBHoney),
* [ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot),
* [citrixhoneypot](https://github.com/MalwareTech/CitrixHoneypot),
* [conpot](http://conpot.org/),
* [cowrie](https://github.com/cowrie/cowrie),
* [dionaea](https://github.com/DinoTools/dionaea),
* [elasticpot](https://github.com/schmalle/ElasticpotPY),
* [glutton](https://github.com/mushorg/glutton),
* [heralding](https://github.com/johnnykv/heralding),
* [honeypy](https://github.com/foospidy/HoneyPy),
* [honeytrap](https://github.com/armedpot/honeytrap/),
* [mailoney](https://github.com/awhitehatter/mailoney),
* [medpot](https://github.com/schmalle/medpot),
* [rdpy](https://github.com/citronneur/rdpy),
* [snare](http://mushmush.org/),
* [tanner](http://mushmush.org/)


Furthermore we use the following tools

* [Cockpit](https://cockpit-project.org/running) for a lightweight, webui for docker, os, real-time performance monitoring and web terminal.
* [Cyberchef](https://gchq.github.io/CyberChef/) a web app for encryption, encoding, compression and data analysis.
* [ELK stack](https://www.elastic.co/videos) to beautifully visualize all the events captured by T-Pot.
* [Elasticsearch Head](https://mobz.github.io/elasticsearch-head/) a web front end for browsing and interacting with an Elastic Search cluster.
* [Fatt](https://github.com/0x4D31/fatt) a pyshark based script for extracting network metadata and fingerprints from pcap files and live network traffic.
* [Spiderfoot](https://github.com/smicallef/spiderfoot) a open source intelligence automation tool.
* [Suricata](http://suricata-ids.org/) a Network Security Monitoring engine.


# TL;DR
1. Meet the [system requirements](#requirements). The T-Pot installation needs at least 6-8 GB RAM and 128 GB free disk space as well as a working internet connection.
2. Download the T-Pot ISO from [GitHub](https://github.com/dtag-dev-sec/tpotce/releases) or [create it yourself](#createiso).
3. Install the system in a [VM](#vm) or on [physical hardware](#hw) with [internet access](#placement).
4. Enjoy your favorite beverage - [watch](https://sicherheitstacho.eu) and [analyze](#kibana).


# Table of Contents
- [Changelog](#changelog)
- [Technical Concept](#concept)
- [System Requirements](#requirements)
- [Installation](#installation)
  - [Prebuilt ISO Image](#prebuilt)
  - [Create your own ISO Image](#createiso)
  - [Running in a VM](#vm)
  - [Running on Hardware](#hardware)
  - [Post Install User](#postinstall)
  - [Post Install Auto](#postinstallauto)
  - [Cloud Deployments](#cloud)
    - [Ansible](#ansible)
    - [Terraform](#terraform)
  - [First Run](#firstrun)
  - [System Placement](#placement)
- [Updates](#updates)
- [Options](#options)
  - [SSH and web access](#ssh)
  - [Kibana Dashboard](#kibana)
  - [Tools](#tools)
  - [Maintenance](#maintenance)
  - [Community Data Submission](#submission)
  - [Opt-In HPFEEDS Data Submission](#hpfeeds-optin)
- [Roadmap](#roadmap)
- [Disclaimer](#disclaimer)
- [FAQ](#faq)
- [Contact](#contact)
- [Licenses](#licenses)
- [Credits](#credits)
- [Stay tuned](#staytuned)
- [Testimonial](#testimonial)
- [Fun Fact](#funfact)

<a name="changelog"></a>
# Release Notes
- **Move from Ubuntu 18.04 to Debian (Sid)**
  - For almost 5 years Ubuntu LTS versions were our distributions of choice. Last year we made a design choice for T-Pot to be closer to a rolling release model and thus allowing us to issue smaller changes and releases in a more timely manner. The distribution of choice is Debian (Sid / unstable) which will provide us with the latest advancements in a Debian based distribution.
- **Include HoneyPy honeypot**
  - *HoneyPy* is now included in the NEXTGEN installation type
- **Include Suricata 4.1.3**
  - Building *Suricata 4.1.3* from scratch to enable JA3 and overall better protocol support.
- **Update tools to the latest versions**
  - ELK Stack 6.6.2
  - CyberChef 8.27.0
  - SpiderFoot v3.0
  - Cockpit 188
  - NGINX is now built to enforce TLS 1.3 on the T-Pot WebUI
- **Update honeypots**
  - Where possible / feasible the honeypots have been updated to their latest versions.
  - *Cowrie* now supports *HASSH* generated hashes which allows for an easier identification of an attacker accross IP adresses.
  - *Heralding* now supports *SOCKS5* emulation.
- **Update Dashboards & Visualizations**
  - *Offset Dashboard* added to easily spot changes in attacks on a single dashboard in 24h time window.
  - *Cowrie Dashboard* modified to integrate *HASSH* support / visualizations.
  - *HoneyPy Dashboard* added to support latest honeypot addition.
  - *Suricata Dashboard* modified to integrate *JA3* support / visualizations.
- **Debian mirror selection**
  - During base install you now have to manually select a mirror.
  - Upon T-Pot install the mirror closest to you will be determined automatically, `netselect-apt` requires you to allow ICMP outbound.
  - This solves peering problems for most of the users speeding up installation and updates.
- **Bugs**
  - Fixed issue #298 where the import and export of objects on the shell did not work.
  - Fixed issue #313 where Spiderfoot raised a KeyError, which was previously fixed in upstream.
  - Fixed error in Suricata where path for reference.config changed.
- **Release Cycle**
  - As far as possible we will integrate changes now faster into the master branch, eliminating the need for monolithic releases. The update feature will be continuously improved on that behalf. However this might not account for all feature changes.
- **HPFEEDS Opt-In**
  - If you want to share your T-Pot data with a 3rd party HPFEEDS broker such as [SISSDEN](https://sissden.eu) you can do so by creating an account at the SISSDEN portal and run `hpfeeds_optin.sh` on T-Pot.
- **Update Feature**
  - For the ones who like to live on the bleeding edge of T-Pot development there is now an update script available in `/opt/tpot/update.sh`.
  - This feature is beta and is mostly intended to provide you with the latest development advances without the need of reinstalling T-Pot.
- **Deprecated tools**
  - *ctop* will no longer be part of T-Pot.
- **Fix #332**
  - If T-Pot, opposed to the requirements, does not have full internet access netselect-apt fails to determine the fastest mirror as it needs ICMP and UDP outgoing. Should netselect-apt fail the default mirrors will be used.
- **Improve install speed with apt-fast**
  - Migrating from a stable base install to Debian (Sid) requires downloading lots of packages. Depending on your geo location the download speed was already improved by introducing netselect-apt to determine the fastest mirror. With apt-fast the downloads will be even faster by downloading packages not only in parallel but also with multiple connections per package.
- **HPFEEDS Opt-In commandline option**
  - Pass a hpfeeds config file as a commandline argument
  - hpfeeds config is saved in `/data/ews/conf/hpfeeds.cfg`
  - Update script restores hpfeeds config
- **Ansible T-Pot Deployment**
  - Transitioned from bash script to all Ansible
  - Reusable Ansible Playbook for OpenStack clouds
  - Example Showcase with our Open Telekom Cloud
  - Adaptable for other cloud providers

<a name="concept"></a>
# Technical Concept

T-Pot is based on the network installer Debian (Stretch). During installation the whole system will be updated to Debian (Sid).
The honeypot daemons as well as other support components being used have been containerized using [docker](http://docker.io).
This allows us to run multiple honeypot daemons on the same network interface while maintaining a small footprint and constrain each honeypot within its own environment.

In T-Pot we combine the dockerized honeypots ...
* [adbhoney](https://github.com/huuck/ADBHoney),
* [ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot),
* [citrixhoneypot](https://github.com/MalwareTech/CitrixHoneypot),
* [conpot](http://conpot.org/),
* [cowrie](http://www.micheloosterhof.com/cowrie/),
* [dionaea](https://github.com/DinoTools/dionaea),
* [elasticpot](https://github.com/schmalle/ElasticpotPY),
* [glutton](https://github.com/mushorg/glutton),
* [heralding](https://github.com/johnnykv/heralding),
* [honeypy](https://github.com/foospidy/HoneyPy),
* [honeytrap](https://github.com/armedpot/honeytrap/),
* [mailoney](https://github.com/awhitehatter/mailoney),
* [medpot](https://github.com/schmalle/medpot),
* [rdpy](https://github.com/citronneur/rdpy),
* [snare](http://mushmush.org/),
* [tanner](http://mushmush.org/)

... with the following tools ...
* [Cockpit](https://cockpit-project.org/running) for a lightweight, webui for docker, os, real-time performance monitoring and web terminal.
* [Cyberchef](https://gchq.github.io/CyberChef/) a web app for encryption, encoding, compression and data analysis.
* [ELK stack](https://www.elastic.co/videos) to beautifully visualize all the events captured by T-Pot.
* [Elasticsearch Head](https://mobz.github.io/elasticsearch-head/) a web front end for browsing and interacting with an Elastic Search cluster.
* [Fatt](https://github.com/0x4D31/fatt) a pyshark based script for extracting network metadata and fingerprints from pcap files and live network traffic.
* [Spiderfoot](https://github.com/smicallef/spiderfoot) a open source intelligence automation tool.
* [Suricata](http://suricata-ids.org/) a Network Security Monitoring engine.

... to give you the best out-of-the-box experience possible and an easy-to-use multi-honeypot appliance.

![Architecture](doc/architecture.png)

While data within docker containers is volatile we do ensure a default 30 day persistence of all relevant honeypot and tool data in the well known `/data` folder and sub-folders. The persistence configuration may be adjusted in `/opt/tpot/etc/logrotate/logrotate.conf`. Once a docker container crashes, all other data produced within its environment is erased and a fresh instance is started from the corresponding docker image.<br>

Basically, what happens when the system is booted up is the following:

- start host system
- start all the necessary services (i.e. cockpit, docker, etc.)
- start all docker containers via docker-compose (honeypots, nms, elk, etc.)

Within the T-Pot project, we provide all the tools and documentation necessary to build your own honeypot system and contribute to our [Sicherheitstacho](https://sicherheitstacho.eu).

The source code and configuration files are fully stored in the T-Pot GitHub repository. The docker images are pre-configured for the T-Pot environment. If you want to run the docker images separately, make sure you study the docker-compose configuration (`/opt/tpot/etc/tpot.yml`) and the T-Pot systemd script (`/etc/systemd/system/tpot.service`), as they provide a good starting point for implementing changes.

The individual docker configurations are located in the [docker folder](https://github.com/dtag-dev-sec/tpotce/tree/master/docker).

<a name="requirements"></a>
# System Requirements
Depending on your installation type, whether you install on [real hardware](#hardware) or in a [virtual machine](#vm), make sure your designated T-Pot system meets the following requirements:

##### Standard Installation
- Honeypots: adbhoney, ciscoasa, conpot, cowrie, dionaea, elasticpot, heralding, honeytrap, mailoney, medpot, rdpy, snare & tanner
- Tools: cockpit, cyberchef, ELK, elasticsearch head, ewsposter, NGINX, spiderfoot, p0f and suricata

- 6-8 GB RAM (less RAM is possible but might introduce swapping)
- 128 GB SSD (smaller is possible but limits the capacity of storing events)
- Network via DHCP
- A working, non-proxied, internet connection

##### Sensor Installation
- Honeypots: adbhoney, ciscoasa, conpot, cowrie, dionaea, elasticpot, heralding, honeytrap, mailoney, medpot, rdpy, snare & tanner
- Tools: cockpit

- 6-8 GB RAM (less RAM is possible but might introduce swapping)
- 128 GB SSD (smaller is possible but limits the capacity of storing events)
- Network via DHCP
- A working, non-proxied, internet connection

##### Industrial Installation
- Honeypots: conpot, cowrie, heralding, medpot, rdpy
- Tools: cockpit, cyberchef, ELK, elasticsearch head, ewsposter, NGINX, spiderfoot, p0f and suricata

- 6-8 GB RAM (less RAM is possible but might introduce swapping)
- 128 GB SSD (smaller is possible but limits the capacity of storing events)
- Network via DHCP
- A working, non-proxied, internet connection

##### Collector Installation (because sometimes all you want to do is catching credentials)
- Honeypots: heralding
- Tools: cockpit, cyberchef, ELK, elasticsearch head, ewsposter, NGINX, spiderfoot, p0f and suricata

- 6-8 GB RAM (less RAM is possible but might introduce swapping)
- 128 GB SSD (smaller is possible but limits the capacity of storing events)
- Network via DHCP
- A working, non-proxied, internet connection

##### NextGen Installation (Glutton replacing Honeytrap, HoneyPy replacing Elasticpot)
- Honeypots: adbhoney, ciscoasa, citrixhoneypot, conpot, cowrie, dionaea, glutton, heralding, honeypy, mailoney, rdpy, snare & tanner
- Tools: cockpit, cyberchef, ELK, elasticsearch head, ewsposter, fatt, NGINX, spiderfoot, p0f and suricata

- 6-8 GB RAM (less RAM is possible but might introduce swapping)
- 128 GB SSD (smaller is possible but limits the capacity of storing events)
- Network via DHCP
- A working, non-proxied, internet connection

<a name="installation"></a>
# Installation
The installation of T-Pot is straight forward and heavily depends on a working, transparent and non-proxied up and running internet connection. Otherwise the installation **will fail!**

Firstly, decide if you want to download our prebuilt installation ISO image from [GitHub](https://github.com/dtag-dev-sec/tpotce/releases), [create it yourself](#createiso) ***or*** [post-install on an existing Debian 9.7 (Stretch)](#postinstall).

Secondly, decide where you want to let the system run: [real hardware](#hardware) or in a [virtual machine](#vm)?

<a name="prebuilt"></a>
## Prebuilt ISO Image
We provide an installation ISO image for download (~50MB), which is created using the same [tool](https://github.com/dtag-dev-sec/tpotce) you can use yourself in order to create your own image. It will basically just save you some time downloading components and creating the ISO image.
You can download the prebuilt installation image from [GitHub](https://github.com/dtag-dev-sec/tpotce/releases) and jump to the [installation](#vm) section.

<a name="createiso"></a>
## Create your own ISO Image
For transparency reasons and to give you the ability to customize your install, we provide you the [ISO Creator](https://github.com/dtag-dev-sec/tpotce) that enables you to create your own ISO installation image.

**Requirements to create the ISO image:**
- Debian 9.7 or newer as host system (others *may* work, but *remain* untested)
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

It is important to make sure you meet the [system requirements](#requirements) and assign a virtual harddisk and RAM according to the requirements while making sure networking is bridged.

You need to enable promiscuous mode for the network interface for suricata and p0f to work properly. Make sure you enable it during configuration.

If you want to use a wifi card as a primary NIC for T-Pot, please be aware of the fact that not all network interface drivers support all wireless cards. E.g. in VirtualBox, you then have to choose the *"MT SERVER"* model of the NIC.

Lastly, mount the `tpot.iso` ISO to the VM and continue with the installation.<br>

You can now jump [here](#firstrun).

<a name="hardware"></a>
## Running on Hardware
If you decide to run T-Pot on dedicated hardware, just follow these steps:

1. Burn a CD from the ISO image or make a bootable USB stick using the image. <br>
Whereas most CD burning tools allow you to burn from ISO images, the procedure to create a bootable USB stick from an ISO image depends on your system. There are various Windows GUI tools available, e.g. [this tip](http://www.ubuntu.com/download/desktop/create-a-usb-stick-on-windows) might help you.<br> On [Linux](http://askubuntu.com/questions/59551/how-to-burn-a-iso-to-a-usb-device) or [MacOS](http://www.ubuntu.com/download/desktop/create-a-usb-stick-on-mac-osx) you can use the tool *dd* or create the USB stick with T-Pot's [ISO Creator](https://github.com/dtag-dev-sec).
2. Boot from the USB stick and install.

*Please note*: While we are performing limited tests with the Intel NUC platform other hardware platforms **remain untested**. We can not provide hardware support of any kind.

<a name="postinstall"></a>
## Post-Install User
In some cases it is necessary to install Debian 9.7 (Stretch) on your own:
 - Cloud provider does not offer mounting ISO images.
 - Hardware setup needs special drivers and / or kernels.
 - Within your company you have to setup special policies, software etc.
 - You just like to stay on top of things.

The T-Pot Universal Installer will upgrade the system to Debian (Sid) and install all required T-Pot dependencies.

Just follow these steps:

```
git clone https://github.com/dtag-dev-sec/tpotce
cd tpotce/iso/installer/
./install.sh --type=user
```

The installer will now start and guide you through the install process.

<a name="postinstallauto"></a>
## Post-Install Auto
You can also let the installer run automatically if you provide your own `tpot.conf`. An example is available in `tpotce/iso/installer/tpot.conf.dist`. This should make things easier in case you want to automate the installation i.e. with **Ansible**.

Just follow these steps while adjusting `tpot.conf` to your needs:

```
git clone https://github.com/dtag-dev-sec/tpotce
cd tpotce/iso/installer/
cp tpot.conf.dist tpot.conf
./install.sh --type=auto --conf=tpot.conf
```

The installer will start automatically and guide you through the install process.

<a name="cloud"></a>
## Cloud Deployments
Located in the [`cloud`](cloud) folder.  
Currently there are examples with Ansible & Terraform.  
If you would like to contribute, you can add other cloud deployments like Chef or Puppet or extend current methods with other cloud providers.

<a name="ansible"></a>
### Ansible Deployment
You can find an [Ansible](https://www.ansible.com/) based T-Pot deployment in the [`cloud/ansible`](cloud/ansible) folder.  
The Playbook in the [`cloud/ansible/openstack`](cloud/ansible/openstack) folder is reusable for all OpenStack clouds out of the box.

It first creates all resources (security group, network, subnet, router), deploys a new server and then installs and configures T-Pot.

You can have a look at the Playbook and easily adapt the deploy role for other [cloud providers](https://docs.ansible.com/ansible/latest/modules/list_of_cloud_modules.html).

<a name="terraform"></a>
### Terraform Configuration

You can find [Terraform](https://www.terraform.io/) configuration in the [`cloud/terraform`](cloud/terraform) folder.

This can be used to launch a virtual machine, bootstrap any dependencies and install T-Pot in a single step.

Configuration for Amazon Web Services (AWS) is currently included and this can easily be extended to support other [Terraform providers](https://www.terraform.io/docs/providers/index.html).

<a name="firstrun"></a>
## First Run
The installation requires very little interaction, only a locale and keyboard setting have to be answered for the basic linux installation. The system will reboot and please maintain the active internet connection. The T-Pot installer will start and ask you for an installation type, password for the **tsec** user and credentials for a **web user**. Everything else will be configured automatically. All docker images and other componenents will be downloaded. Depending on your network connection and the chosen installation type, the installation may take some time. During our tests (250Mbit down, 40Mbit up), the installation was usually finished within a 15-30 minute timeframe.

Once the installation is finished, the system will automatically reboot and you will be presented with the T-Pot login screen. On the console you may login with:

- user: **[tsec or user]** *you chose during one of the post install methods*
- pass: **[password]** *you chose during the installation*

All honeypot services are preconfigured and are starting automatically.

You can login from your browser and access the Admin UI: `https://<your.ip>:64294` or via SSH to access the command line: `ssh -l tsec -p 64295 <your.ip>`

- user: **[tsec or user]** *you chose during one of the post install methods*
- pass: **[password]** *you chose during the installation*

You can also login from your browser and access the Web UI: `https://<your.ip>:64297`
- user: **[user]** *you chose during the installation*
- pass: **[password]** *you chose during the installation*


<a name="placement"></a>
# System Placement
Make sure your system is reachable through a network you suspect intruders in / from (i.e. the internet). Otherwise T-Pot will most likely not capture any attacks, other than the ones from your  internal network! We recommend you put it in an unfiltered zone, where all TCP and UDP traffic is forwarded to T-Pot's network interface. However to avoid fingerprinting you can put T-Pot behind a firewall and forward all TCP / UDP traffic in the port range of 1-64000 to T-Pot while allowing access to ports > 64000 only from trusted IPs.

A list of all relevant ports is available as part of the [Technical Concept](#concept)
<br>

Basically, you can forward as many TCP ports as you want, as honeytrap dynamically binds any TCP port that is not covered by the other honeypot daemons.

In case you need external Admin UI access, forward TCP port 64294 to T-Pot, see below.
In case you need external SSH access, forward TCP port 64295 to T-Pot, see below.
In case you need external Web UI access, forward TCP port 64297 to T-Pot, see below.

T-Pot requires outgoing git, http, https connections for updates (Debian, Docker, GitHub, PyPi) and attack submission (ewsposter, hpfeeds). Ports and availability may vary based on your geographical location.

<a name="updates"></a>
# Updates
For the ones of you who want to live on the bleeding edge of T-Pot development we introduced an update feature which will allow you to update all T-Pot relevant files to be up to date with the T-Pot master branch.
**If you made any relevant changes to the T-Pot relevant config files make sure to create a backup first.**

The Update script will:
 - **mercilessly** overwrite local changes to be in sync with the T-Pot master branch
 - upgrade the system to the packages available in Debian (Sid)
 - update all resources to be in-sync with the T-Pot master branch
 - ensure all T-Pot relevant system files will be patched / copied into the original T-Pot state
 - restore your custom ews.cfg and HPFEED settings from `/data/ews/conf`

You simply run the update script:
```
sudo su -
cd /opt/tpot/
./update.sh -y
```

**Despite all our efforts please be reminded that updates sometimes may have unforeseen consequences. Please create a backup of the machine or the files with the most value to your work.**  

<a name="options"></a>
# Options
The system is designed to run without any interaction or maintenance and automatically contributes to the community.<br>
We know, for some this may not be enough. So here come some ways to further inspect the system and change configuration parameters.

<a name="ssh"></a>
## SSH and web access
By default, the SSH daemon allows access on **tcp/64295** with a user / password combination and prevents credential brute forcing attempts using `fail2ban`. This also counts for Admin UI (**tcp/64294**) and Web UI (**tcp/64297**) access.<br>

If you do not have a SSH client at hand and still want to access the machine via command line you can do so by accessing the Admin UI from `https://<your.ip>:64294`, enter

- user: **[tsec or user]** *you chose during one of the post install methods*
- pass: **[password]** *you chose during the installation*

![Cockpit Terminal](doc/cockpit3.png)

<a name="kibana"></a>
## Kibana Dashboard
Just open a web browser and connect to `https://<your.ip>:64297`, enter

- user: **[user]** *you chose during the installation*
- pass: **[password]** *you chose during the installation*

and **Kibana** will automagically load. The Kibana dashboard can be customized to fit your needs. By default, we haven't added any filtering, because the filters depend on your setup. E.g. you might want to filter out your incoming administrative ssh connections and connections to update servers.

![Dashbaord](doc/kibana.png)

<a name="tools"></a>
## Tools
We included some web based management tools to improve and ease up on your daily tasks.

![Cockpit Overview](doc/cockpit1.png)

![Cockpit Containers](doc/cockpit2.png)

![Cyberchef](doc/cyberchef.png)

![ES Head Plugin](doc/headplugin.png)

![Spiderfoot](doc/spiderfoot.png)


<a name="maintenance"></a>
## Maintenance
As mentioned before, the system is designed to be low maintenance. Basically, there is nothing you have to do but let it run.

If you run into any problems, a reboot may fix it :bowtie:

If new versions of the components involved appear, we will test them and build new docker images. Those new docker images will be pushed to docker hub and downloaded to T-Pot and activated accordingly.  

<a name="submission"></a>
## Community Data Submission
We provide T-Pot in order to make it accessible to all parties interested in honeypot deployment. By default, the captured data is submitted to a community backend. This community backend uses the data to feed [Sicherheitstacho](https://sicherheitstacho.eu).
You may opt out of the submission by removing the `# Ewsposter service` from `/opt/tpot/etc/tpot.yml`:
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
    image: "dtagdevsec/ewsposter:1903"
    volumes:
     - /data:/data
     - /data/ews/conf/ews.ip:/opt/ewsposter/ews.ip
```
4. Start T-Pot services: `systemctl start tpot`

Data is submitted in a structured ews-format, a XML stucture. Hence, you can parse out the information that is relevant to you.

We encourage you not to disable the data submission as it is the main purpose of the community approach - as you all know **sharing is caring** 😍

<a name="hpfeeds-optin"></a>
## Opt-In HPFEEDS Data Submission
As an Opt-In it is now possible to also share T-Pot data with 3rd party HPFEEDS brokers, such as [SISSDEN](https://sissden.eu).  
If you want to share your T-Pot data you simply have to register an account with a 3rd party broker with its own benefits towards the community. Once registered you will receive your credentials to share events with the broker. In T-Pot you simply run `hpfeeds_optin.sh` which will ask for your credentials, in case of SISSDEN this is just `Ident` and `Secret`, everything else is pre-configured.  
It will automatically update `/opt/tpot/etc/tpot.yml` to deliver events to your desired broker.

The script can accept a config file as an argument, e.g. `./hpfeeds_optin.sh --conf=hpfeeds.cfg`

Your current config will also be stored in `/data/ews/conf/hpfeeds.cfg` where you can review or change it.  
Be sure to apply any changes by running `./hpfeeds_optin.sh --conf=/data/ews/conf/hpfeeds.cfg`.  
No worries: Your old config gets backed up in `/data/ews/conf/hpfeeds.cfg.old`

Of course you can also rerun the `hpfeeds_optin.sh` script to change and apply your settings interactively.

<a name="roadmap"></a>
# Roadmap
As with every development there is always room for improvements ...

Some features may be provided with updated docker images, others may require some hands on from your side.

You are always invited to participate in development on our [GitHub](https://github.com/dtag-dev-sec/tpotce) page.

<a name="disclaimer"></a>
# Disclaimer
- We don't have access to your system. So we cannot remote-assist when you break your configuration. But you can simply reinstall.
- The software was designed with best effort security, not to be in stealth mode. Because then, we probably would not be able to provide those kind of honeypot services.
- You install and you run within your responsibility. Choose your deployment wisely as a system compromise can never be ruled out.
- Honeypots should - by design - may not host any sensitive data. Make sure you don't add any.
- By default, your data is submitted to the community dashboard. You can disable this in the config. But hey, wouldn't it be better to contribute to the community?

<a name="faq"></a>
# FAQ
Please report any issues or questions on our [GitHub issue list](https://github.com/dtag-dev-sec/tpotce/issues), so the community can participate.

<a name="contact"></a>
# Contact
We provide the software **as is** in a Community Edition format. T-Pot is designed to run out of the box and with zero maintenance involved. <br>
We hope you understand that we cannot provide support on an individual basis. We will try to address questions, bugs and problems on our [GitHub issue list](https://github.com/dtag-dev-sec/tpotce/issues).

<a name="licenses"></a>
# Licenses
The software that T-Pot is built on uses the following licenses.
<br>GPLv2: [conpot](https://github.com/mushorg/conpot/blob/master/LICENSE.txt), [dionaea](https://github.com/DinoTools/dionaea/blob/master/LICENSE), [honeypy](https://github.com/foospidy/HoneyPy/blob/master/LICENSE), [honeytrap](https://github.com/armedpot/honeytrap/blob/master/LICENSE), [suricata](http://suricata-ids.org/about/open-source/)
<br>GPLv3: [adbhoney](https://github.com/huuck/ADBHoney), [elasticpot](https://github.com/schmalle/ElasticpotPY), [ewsposter](https://github.com/dtag-dev-sec/ews/), [fatt](https://github.com/0x4D31/fatt/blob/master/LICENSE), [rdpy](https://github.com/citronneur/rdpy/blob/master/LICENSE), [heralding](https://github.com/johnnykv/heralding/blob/master/LICENSE.txt), [snare](https://github.com/mushorg/snare/blob/master/LICENSE), [tanner](https://github.com/mushorg/snare/blob/master/LICENSE)
<br>Apache 2 License: [cyberchef](https://github.com/gchq/CyberChef/blob/master/LICENSE), [elasticsearch](https://github.com/elasticsearch/elasticsearch/blob/master/LICENSE.txt), [logstash](https://github.com/elasticsearch/logstash/blob/master/LICENSE), [kibana](https://github.com/elasticsearch/kibana/blob/master/LICENSE.md), [docker](https://github.com/docker/docker/blob/master/LICENSE), [elasticsearch-head](https://github.com/mobz/elasticsearch-head/blob/master/LICENCE)
<br>MIT license: [ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot/blob/master/LICENSE), [glutton](https://github.com/mushorg/glutton/blob/master/LICENSE)
<br> Other: [citrixhoneypot](https://github.com/MalwareTech/CitrixHoneypot#licencing-agreement-malwaretech-public-licence), [cowrie](https://github.com/micheloosterhof/cowrie/blob/master/LICENSE.md), [mailoney](https://github.com/awhitehatter/mailoney), [Debian licensing](https://www.debian.org/legal/licenses/)

<a name="credits"></a>
# Credits
Without open source and the fruitful development community (we are proud to be a part of), T-Pot would not have been possible! Our thanks are extended but not limited to the following people and organizations:

### The developers and development communities of

* [adbhoney](https://github.com/huuck/ADBHoney/graphs/contributors)
* [apt-fast](https://github.com/ilikenwf/apt-fast/graphs/contributors)
* [ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot/graphs/contributors)
* [citrixhoneypot](https://github.com/MalwareTech/CitrixHoneypot/graphs/contributors)
* [cockpit](https://github.com/cockpit-project/cockpit/graphs/contributors)
* [conpot](https://github.com/mushorg/conpot/graphs/contributors)
* [cowrie](https://github.com/micheloosterhof/cowrie/graphs/contributors)
* [debian](http://www.debian.org/)
* [dionaea](https://github.com/DinoTools/dionaea/graphs/contributors)
* [docker](https://github.com/docker/docker/graphs/contributors)
* [elasticpot](https://github.com/schmalle/ElasticpotPY/graphs/contributors)
* [elasticsearch](https://github.com/elastic/elasticsearch/graphs/contributors)
* [elasticsearch-head](https://github.com/mobz/elasticsearch-head/graphs/contributors)
* [ewsposter](https://github.com/armedpot/ewsposter/graphs/contributors)
* [fatt](https://github.com/0x4D31/fatt/graphs/contributors)
* [glutton](https://github.com/mushorg/glutton/graphs/contributors)
* [heralding](https://github.com/johnnykv/heralding/graphs/contributors)
* [honeypy](https://github.com/foospidy/HoneyPy/graphs/contributors)
* [honeytrap](https://github.com/armedpot/honeytrap/graphs/contributors)
* [kibana](https://github.com/elastic/kibana/graphs/contributors)
* [logstash](https://github.com/elastic/logstash/graphs/contributors)
* [mailoney](https://github.com/awhitehatter/mailoney)
* [medpot](https://github.com/schmalle/medpot/graphs/contributors)
* [p0f](http://lcamtuf.coredump.cx/p0f3/)
* [rdpy](https://github.com/citronneur/rdpy)
* [spiderfoot](https://github.com/smicallef/spiderfoot)
* [snare](https://github.com/mushorg/snare/graphs/contributors)
* [tanner](https://github.com/mushorg/tanner/graphs/contributors)
* [suricata](https://github.com/inliniac/suricata/graphs/contributors)

### The following companies and organizations
* [debian](https://www.debian.org/)
* [docker](https://www.docker.com/)
* [elastic.io](https://www.elastic.co/)
* [honeynet project](https://www.honeynet.org/)
* [intel](http://www.intel.com)

### ... and of course ***you*** for joining the community!

<a name="staytuned"></a>
# Stay tuned ...
We will be releasing a new version of T-Pot about every 6-12 months.

<a name="testimonial"></a>
# Testimonial
One of the greatest feedback we have gotten so far is by one of the Conpot developers:<br>
***"[...] I highly recommend T-Pot which is ... it's not exactly a swiss army knife .. it's more like a swiss army soldier, equipped with a swiss army knife. Inside a tank. A swiss tank. [...]"***

<a name="funfact"></a>
# Fun Fact

In an effort of saving the environment we are now brewing our own Mate Ice Tea and consumed 73 liters so far for the T-Pot 19.03 development 😇
