# T-Pot - The All In One Multi Honeypot Plattform

![T-Pot](doc/tpotsocial.png)

T-Pot is the all in one, optionally distributed, multiarch (amd64, arm64) honeypot plattform, supporting 20+ honeypots and countless visualization options using the Elastic Stack, animated live attack maps and lots of security tools to further improve the deception experience.

T-Pot is based on the Debian 11 (Bullseye) Netinstaller and utilizes 
[docker](https://www.docker.com/) and [docker-compose](https://docs.docker.com/compose/) to reach its goal of running as many tools as possible simultaneously and thus utilizing the host's hardware to its maximum.
<br><br>

# TL;DR
1. Meet the [system requirements](#requirements). The T-Pot installation needs at least 8-16 GB RAM and 128 GB free disk space as well as a working (outgoing non-filtered) internet connection.
2. Download the T-Pot ISO from [GitHub](https://github.com/telekom-security/tpotce/releases) acording to your architecture (amd64, arm64) or [create it yourself](#createiso).
3. Install the system in a [VM](#vm) or on [physical hardware](#hw) with [internet access](#placement).
4. Enjoy your favorite beverage - [watch](https://sicherheitstacho.eu) and [analyze](#kibana).
<br><br>

# Table of Contents
- [Disclaimer](#disclaimer)
- [Technical Concept](#technical-concept)
  - [Technical Architecture](#technical-architecture)
  - [Services](#services)
  - [User Types](#user-types)
- [System Requirements](#system-requirements)
  - [Running in a VM](#running-in-a-vm)
  - [Running on Hardware](#running-on-hardware)
  - [Running in a Cloud](#running-in-a-cloud)
  - [Required Ports](#required-ports)
- [System Placement](#system-placement)
- [Installation](#installation)
  - [ISO Based](#isoinstall)
    - [Download ISO Image](#downloadiso)
    - [Build your own ISO Image](#makeiso)
  - [T-Pot Installer](#tpotinstaller)
    - [Installation Types](#installtypes)
    - [Standalone](#standalonetype)
    - [Distributed](#distributedtype)
  - [Post Install](#postinstall)
    - [Download Debian Netinstall Image](#downloadnetiso)
    - [User](#postuser)
    - [Auto](#postauto)
  - [Cloud Deployments](#cloud)
    - [Ansible](#ansible)
    - [Terraform](#terraform)
  - [Community Data Submission](#ews)
  - [Opt-In HPFEEDS Data Submission](#hpfeeds-optin)
- [Operations](#ops)
  - [First Start](#firststart)
    - [Standalone](#standalone1st)
    - [Distributed](#distributed1st)
  - [Remote Access & Tools](#access)
    - [SSH and Cockpit](#ssh)
    - [T-Pot Landing Page](#tpotwebui)
    - [Kibana Dashboard](#kibana)
    - [Attack Map](#attackmap)
    - [Cyberchef](#cyberchef)
    - [Elasticvue](#elasticvue)
    - [Spiderfoot](#spiderfoot)
  - [Maintenance](#maintenance)
    - [Start T-Pot](#starttpot)
    - [Stop T-Pot](#stoptpot)
    - [T-Pot Data Folder](#datafolder)
    - [Log Persistence](#datafolder)
    - [Clean Up](#cleanup)
    - [Show Containers](#showcontainers)
    - [Blackhole](#blackhole)
    - [Add user](#adduser)
    - [Import objects](#import)
    - [Switch editions](#switcheditions)
    - [Redeploy Hive Sensor](#redeploy)
    - [Adjust tpot.yml](#adjusttpot)
    - [Enable 2FA](#enable2fa)
  - [Troubleshooting](#troubleshooting)
    - [Logging](#logging)
    - [Fail2Ban](#fail2ban)
    - [RAM](#logging)
  - [Updates](#updates)
- [Contact](#contact)
  - [Discussions](#discussions)
  - [Issues](#issues)
- [Licenses](#licenses)
- [Credits](#credits)
- [Testimonials](#testimonials)
<br><br>

# Disclaimer
- You install and run T-Pot within your responsibility. Choose your deployment wisely as a system compromise can never be ruled out.
- For fast help research the [Issues](https://github.com/telekom-security/tpotce/issues) and [Discussions](https://github.com/telekom-security/tpotce/discussions).
- The software is designed and offered with best effort in mind. As a community and open source project it uses lots of other open source software and may contain bugs and issues. Report responsibly.
- Honeypots - by design - should not host any sensitive data. Make sure you don't add any.
- By default, your data is submitted to [SecurityMeter](https://www.sicherheitstacho.eu/start/main). You can disable this in the config (`/opt/tpot/etc/tpot.yml`) by remove the ewsposter section. But in this case sharing really is caring!
<br><br>

<a name="technical-concept"></a>
# Technical Concept

T-Pot is based on the Debian Netinstaller and utilizes 
[docker](https://www.docker.com/) and [docker-compose](https://docs.docker.com/compose/) to reach its goal of running as many tools simultaneously as possible and thus utilizing the host's hardware to its maximum.
<br><br>

T-Pot offers docker images for the following honeypots ...
* [adbhoney](https://github.com/huuck/ADBHoney),
* [ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot),
* [citrixhoneypot](https://github.com/MalwareTech/CitrixHoneypot),
* [conpot](http://conpot.org/),
* [cowrie](http://www.micheloosterhof.com/cowrie/),
* [ddospot](https://github.com/aelth/ddospot),
* [dicompot](https://github.com/nsmfoo/dicompot),
* [dionaea](https://github.com/DinoTools/dionaea),
* [elasticpot](https://gitlab.com/bontchev/elasticpot),
* [endlessh](https://github.com/skeeto/endlessh),
* [glutton](https://github.com/mushorg/glutton),
* [heralding](https://github.com/johnnykv/heralding),
* [hellpot](https://github.com/yunginnanet/HellPot),
* [honeypots](https://github.com/qeeqbox/honeypots),
* [honeytrap](https://github.com/armedpot/honeytrap/),
* [ipphoney](https://gitlab.com/bontchev/ipphoney),
* [log4pot](https://github.com/thomaspatzke/Log4Pot),
* [mailoney](https://github.com/awhitehatter/mailoney),
* [medpot](https://github.com/schmalle/medpot),
* [redishoneypot](https://github.com/cypwnpwnsocute/RedisHoneyPot),
* [sentrypeer](https://github.com/SentryPeer/SentryPeer),
* [snare](http://mushmush.org/),
* [tanner](http://mushmush.org/)

... alongside the following tools ...
* [Cockpit](https://cockpit-project.org/running) for a lightweight and secure WebManagement and WebTerminal.
* [Cyberchef](https://gchq.github.io/CyberChef/) a web app for encryption, encoding, compression and data analysis.
* [Elastic Stack](https://www.elastic.co/videos) to beautifully visualize all the events captured by T-Pot.
* [Elasticvue](https://github.com/cars10/elasticvue/) a web front end for browsing and interacting with an Elastic Search cluster.
* [Fatt](https://github.com/0x4D31/fatt) a pyshark based script for extracting network metadata and fingerprints from pcap files and live network traffic.
* [Geoip-Attack-Map](https://github.com/eddie4/geoip-attack-map) a beautifully animated attack map [optimized](https://github.com/t3chn0m4g3/geoip-attack-map) for T-Pot.
* [P0f](https://lcamtuf.coredump.cx/p0f3/) P0f is a tool for purely passive traffic fingerprinting.
* [Spiderfoot](https://github.com/smicallef/spiderfoot) a open source intelligence automation tool.
* [Suricata](http://suricata-ids.org/) a Network Security Monitoring engine.

... to give you the best out-of-the-box experience possible and an easy-to-use multi-honeypot appliance.
<br><br>


## Technical Architecture
![Architecture](doc/architecture.svg)

The source code and configuration files are fully stored in the T-Pot GitHub repository. The docker images are built and preconfigured for the T-Pot environment. 

The individual Dockerfiles and configurations are located in the [docker folder](https://github.com/telekom-security/tpotce/tree/master/docker).
<br><br>

## Services
T-Pot offers a number of services which are basically divided into five groups:
1. System services provided by the OS
    * SSH for secure remote access.
    * Cockpit for web based remote acccess, management and web terminal.
2. Elastic Stack
    * Elasticsearch for storing events.
    * Logstash for ingesting, receiving and sending events to Elasticsearch.
    * Kibana for displaying events on beautyfully rendered dashboards.
3. Tools
    * NGINX for providing secure remote access (reverse proxy) to Kibana, CyberChef, Elasticvue, GeoIP AttackMap and Spiderfoot.
    * CyberChef a web app for encryption, encoding, compression and data analysis.
    * Elasticvue a web front end for browsing and interacting with an Elastic Search cluster.
    * Geoip Attack Map a beautifully animated attack map for T-Pot.
    * Spiderfoot a open source intelligence automation tool.
4. Honeypots
    * A selection of the 22 available honeypots based on the selected edition and / or setup.
5. Network Security Monitoring (NSM)
    * Fatt a pyshark based script for extracting network metadata and fingerprints from pcap files and live network traffic.
    * P0f is a tool for purely passive traffic fingerprinting.
    * Suricata a Network Security Monitoring engine.
<br><br>

## User Types
During the installation and during the usage of T-Pot there are two different types of accounts you will be working with. Make sure you know the differences of the different account types, since it is **by far** the most common reason for authentication errors and `fail2ban` lockouts.

| Service             | Account      | Username         | Description                                                             |
| :---                | :---         | :---             | :---                                                                    |
| SSH, Cockpit        | OS           | `tsec`           | On ISO based installations the user `tsec` is predefined.               |
| SSH, Cockpit        | OS           | `<os_username>`  | Any other installation, the `<username>` you chose during installation. |
| Nginx               | BasicAuth    | `<web_user>`     | `<web_user>` you chose during the installation of T-Pot.                |
| CyberChef           | BasicAuth    | `<web_user>`     | `<web_user>` you chose during the installation of T-Pot.                |
| Elasticvue          | BasicAuth    | `<web_user>`     | `<web_user>` you chose during the installation of T-Pot.                |
| Geoip Attack Map    | BasicAuth    | `<web_user>`     | `<web_user>` you chose during the installation of T-Pot.                |
| Spiderfoot          | BasicAuth    | `<web_user>`     | `<web_user>` you chose during the installation of T-Pot.                |
<br><br>

# System Requirements

Depending on the installation setup, edition, installing on [real hardware](#running-on-hardware), in a [virtual machine](#running-in-a-vm) or [cloud](#running-in-a-cloud) there are different kind of requirements to be met regarding OS, RAM, storage and network for a successful installation of T-Pot (you can always adjust `/opt/tpot/etc/tpot.yml` to your needs to overcome these requirements).
<br><br>
| T-Pot Type  | RAM      | Storage         | Description                                                                              |
| :---        | :---     | :---            | :---                                                                                     |
| Standalone  | 8-16GB   | >=128GB SSD     | RAM requirements depend on the edition, storage on how much data you want to persist.    |
| Hive        | >=8GB    | >=256GB SSD     | As a rule of thumb, the more sensors & data, the more RAM and storage is needed.         |
| Hive_Sensor | >=8GB    | >=128GB SSD     | Since honeypot logs are persisted (/data) for 30 days, storage depends on attack volume. |
<br><vr>

Besides that all T-Pot installations will require ...
- an IP address via DHCP
- a working, non-proxied, internet connection

... to work out of the box.
<br>
*If you need proxy support or static IP addresses please review the Debian and Docker documentation.*
<br><br>

## Running in a VM
T-Pot is tested on and known to run with ...
* ESXi
* UTM (Intel & Apple Silicon)
* VMWare Fusion (Intel & Apple Silicon) and Workstation
* VirtualBox

While Intel versions run stable, Apple Silicon (arm64) support for Debian has known issues which in UTM may require switching `Display` to `Console Only` during initial installation of T-Pot / Debian and afterwards back to `Full Graphics`.
<br><br>

## Running on Hardware
T-Pot is tested on and known to run with ...
* IntelNUC series (only some tested)
* Some generic Intel hardware

Since the number of possible hardware combinations is too high to make general recommendations. If you are unsure, you should test the hardware with the T-Pot ISO image or use the post install method.  
<br><br>

## Running in a Cloud
T-Pot is tested on and known to run on ...
* Telekom OTC using the post install method
* Amazon AWS using the post install method (somehow limited)

Some users report working installations on other clouds and hosters, i.e. Azure and GCP. Hardware requirements may be different. If you are unsure you should research [issues](https://github.com/telekom-security/tpotce/issues) and [discussions](https://github.com/telekom-security/tpotce/discussions) and run some functional tests. Cloud support is a community developed feature and hyperscalers are known to adjust linux images, so expect some necessary adjustments on your end. 
<br><br>

## Required Ports
Besides the ports generally needed by the OS, i.e. obtaining a DHCP lease, DNS, etc. T-Pot will require the following ports for incomding / outgoing connections. Review the [T-Pot Architecure](#technical-architecture) for a visual representation. Also some ports will show up as duplicates, which is fine since used in different editions.
| Port        | Protocol | Direction | Description                                                   |
| :---        | :---     | :---      | :---                                                          |
| 80, 443     | tcp      | outgoing  | T-Pot Management: Install, Updates, Logs (i.e. Debian,<br> GitHub, DockerHub, PyPi, Sicherheitstacho, etc. |
| 64294       | tcp      | incoming  | T-Pot Management: Access to Cockpit                           |
| 64295       | tcp      | incoming  | T-Pot Management: Access to SSH                               |
| 64297       | tcp      | incoming  | T-Pot Management Access to NGINX reverse proxy                |
| 5555        | tcp      | incoming  | Honeypot: ADBHoney                                            |
| 5000        | udp      | incoming  | Honeypot: CiscoASA                                            |
| 8443        | tcp      | incoming  | Honeypot: CiscoASA                                            |
| 443         | tcp      | incoming  | Honeypot: CitrixHoneypot                                      |
| 80, 102, 502, 1025, 2404,<br> 10001, 44818, 47808, 50100  | tcp      | incoming          | Honeypot: Conpot            |
| 161, 623    | udp      | incoming  | Honeypot: Conpot                                              |
| 22, 23      | tcp      | incoming  | Honeypot: Cowrie                                              |
| 19, 53, 123, 1900 | udp| incoming  | Honeypot: Ddospot                                             |
| 11112       | tcp      | incoming  | Honeypot: Dicompot                                            |
| 21, 42, 135, 443, 445,<br> 1433, 1723, 1883, 3306, 8081 | tcp        | incoming          | Honeypot: Dionaea           |
| 69          | udp      | incoming  | Honeypot: Dionaea                                             |
| 9200        | tcp      | incoming  | Honeypot: Elasticpot                                          |
| 22          | tcp      | incoming  | Honeypot: Endlessh                                            |
| 21, 22, 23, 25, 80, 110, 143, 443,<br> 993, 995, 1080, 5432, 5900          | tcp      | incoming  | Honeypot: Heralding  |
| 21, 22, 23, 25, 80, 110, 143, 389,<br> 443, 445, 1080, 1433, 1521,<br> 3306, 5432, 5900, 6379,<br> 8080, 9200, 11211 | tcp | incoming  | Honeypot: qHoneypots |
| 53, 123, 161| udp      | incoming  | Honeypot: qHoneypots                                          |
| 631         | tcp      | incoming  | Honeypot: IPPHoney                                            |
| 80, 443, 8080, 9200, 25565 | tcp      | incoming  | Honeypot: Log4Pot                              |
| 25          | tcp      | incoming  | Honeypot: Mailoney                                            |
| 2575        | tcp      | incoming  | Honeypot: Medpot                                              |
| 6379        | tcp      | incoming  | Honeypot: Redishoneypot                                       |
| 5060        | udp      | incoming  | Honeypot: SentryPeer                                          |
| 80          | tcp      | incoming  | Honeypot: Snare (Tanner)                                      |


Ports and availability of SaaS services may vary based on your geographical location. Also during first install outgoing ICMP / TRACEROUTE is required additionally to find the closest and fastest mirror to you.
<br><br>

# System Placement
It is recommended to get yourself familiar how T-Pot and it honeypots work before you start exposing it towards the interet. For a quickstart run a T-Pot installation in a virtual machine.
<br><br>
Once you are familiar how things work you should choose a network you suspect intruders in / from (i.e. the internet). Otherwise T-Pot will most likely not capture any attacks, other than the ones from your internal network! For starters it is recommended to put T-Pot in an unfiltered zone, where all TCP and UDP traffic is forwarded to T-Pot's network interface. However to avoid fingerprinting you can put T-Pot behind a firewall and forward all TCP / UDP traffic in the port range of 1-64000 to T-Pot while allowing access to ports > 64000 only from trusted IPs or only expose the [ports](#required-ports) you want. However if you wish to catch malware traffic on unknown ports you should not limit the ports you forward since glutton & honeytrap dynamically bind any TCP port that is not covered by the other honeypot daemons and thus give you a better representation what risks you are exposed to.
<br><br>

<a name="installation"></a>
# Installation
The installation of T-Pot is straight forward and heavily depends on a working, transparent and non-proxied up and running internet connection. Otherwise the installation **will fail!**

Firstly, decide if you want to download the prebuilt installation ISO image from [GitHub](https://github.com/telekom-security/tpotce/releases), [create it yourself](#createiso) ***or*** [post-install on an existing Debian 10 (Buster)](#postinstall).

Secondly, decide where you the system to run: [real hardware](#hardware) or in a [virtual machine](#vm)?

<a name="prebuilt"></a>
## Prebuilt ISO Image
An installation ISO image is available for download (~50MB), which is created by the [ISO Creator](https://github.com/telekom-security/tpotce) you can use yourself in order to create your own image. It will basically just save you some time downloading components and creating the ISO image.
You can download the prebuilt installation ISO from [GitHub](https://github.com/telekom-security/tpotce/releases) and jump to the [installation](#vm) section.

<a name="createiso"></a>
## Create your own ISO Image
For transparency reasons and to give you the ability to customize your install you use the [ISO Creator](https://github.com/telekom-security/tpotce) that enables you to create your own ISO installation image.

**Requirements to create the ISO image:**
- Debian 10 as host system (others *may* work, but *remain* untested)
- 4GB of free memory  
- 32GB of free storage
- A working internet connection

**How to create the ISO image:**

1. Clone the repository and enter it.
```
git clone https://github.com/telekom-security/tpotce
cd tpotce
```
2. Run the `makeiso.sh` script to build the ISO image.
The script will download and install dependencies necessary to build the image on the invoking machine. It will further download the ubuntu network installer image (~50MB) which T-Pot is based on.
```
sudo ./makeiso.sh
```
After a successful build, you will find the ISO image `tpot.iso` along with a SHA256 checksum `tpot.sha256` in your folder.

<a name="vm"></a>
## Running in VM
You may want to run T-Pot in a virtualized environment. The virtual system configuration depends on your virtualization provider.

T-Pot is successfully tested with [VirtualBox](https://www.virtualbox.org) and [VMWare](http://www.vmware.com) with just little modifications to the default machine configurations.

It is important to make sure you meet the [system requirements](#requirements) and assign virtual harddisk and RAM according to the requirements while making sure networking is bridged.

You need to enable promiscuous mode for the network interface for fatt, suricata and p0f to work properly. Make sure you enable it during configuration.

If you want to use a wifi card as a primary NIC for T-Pot, please be aware that not all network interface drivers support all wireless cards. In VirtualBox e.g. you have to choose the *"MT SERVER"* model of the NIC.

Lastly, mount the `tpot.iso` ISO to the VM and continue with the installation.<br>

You can now jump [here](#firstrun).

<a name="hardware"></a>
## Running on hartware
If you decide to run T-Pot on dedicated hardware, just follow these steps:

1. Burn a CD from the ISO image or make a bootable USB stick using the image. <br>
Whereas most CD burning tools allow you to burn from ISO images, the procedure to create a bootable USB stick from an ISO image depends on your system. There are various Windows GUI tools available, e.g. [this tip](http://www.ubuntu.com/download/desktop/create-a-usb-stick-on-windows) might help you.<br> On [Linux](http://askubuntu.com/questions/59551/how-to-burn-a-iso-to-a-usb-device) or [MacOS](http://www.ubuntu.com/download/desktop/create-a-usb-stick-on-mac-osx) you can use the tool *dd* or create the USB stick with T-Pot's [ISO Creator](https://github.com/telekom-security).
2. Boot from the USB stick and install.

*Please note*: Limited tests are performed for the Intel NUC platform other hardware platforms **remain untested**. There is no hardware support provided of any kind.

<a name="postinstall"></a>
## Post-Install User
In some cases it is necessary to install Debian 10 (Buster) on your own:
 - Cloud provider does not offer mounting ISO images.
 - Hardware setup needs special drivers and / or kernels.
 - Within your company you have to setup special policies, software etc.
 - You just like to stay on top of things.

The T-Pot Universal Installer will upgrade the system and install all required T-Pot dependencies.

Important notice: The user / group `tpot` are reserved for T-Pot. The installation will abort if the user `tpot` exists. Make sure to use a different user name when preparing the OS installation for T-Pot.

Just follow these steps:

```
git clone https://github.com/telekom-security/tpotce
cd tpotce/iso/installer/
./install.sh --type=user
```

The installer will now start and guide you through the install process.

<a name="postinstallauto"></a>
## Post-Install Auto
You can also let the installer run automatically if you provide your own `tpot.conf`. An example is available in `tpotce/iso/installer/tpot.conf.dist`. This should make things easier in case you want to automate the installation i.e. with **Ansible**.

Just follow these steps while adjusting `tpot.conf` to your needs:

```
git clone https://github.com/telekom-security/tpotce
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

*Please note*: Cloud providers usually offer adjusted Debian OS images, which might not be compatible with T-Pot. There is no cloud provider support provided of any kind.

<a name="ansible"></a>
### Ansible Deployment
You can find an [Ansible](https://www.ansible.com/) based T-Pot deployment in the [`cloud/ansible`](cloud/ansible) folder.  
The Playbook in the [`cloud/ansible/openstack`](cloud/ansible/openstack) folder is reusable for all **OpenStack** clouds out of the box.

It first creates all resources (security group, network, subnet, router), deploys one (or more) new servers and then installs and configures T-Pot on them.

You can have a look at the Playbook and easily adapt the deploy role for other [cloud providers](https://docs.ansible.com/ansible/latest/scenario_guides/cloud_guides.html). Check out [Ansible Galaxy](https://galaxy.ansible.com/search?keywords=&order_by=-relevance&page=1&deprecated=false&type=collection&tags=cloud) for more cloud collections.

*Please note*: Cloud providers usually offer adjusted Debian OS images, which might not be compatible with T-Pot. There is no cloud provider support provided of any kind.

<a name="terraform"></a>
### Terraform Configuration

You can find [Terraform](https://www.terraform.io/) configuration in the [`cloud/terraform`](cloud/terraform) folder.

This can be used to launch a virtual machine, bootstrap any dependencies and install T-Pot in a single step.

Configuration for **Amazon Web Services** (AWS) and **Open Telekom Cloud** (OTC) is currently included.  
This can easily be extended to support other [Terraform providers](https://registry.terraform.io/browse/providers?category=public-cloud%2Ccloud-automation%2Cinfrastructure).

*Please note*: Cloud providers usually offer adjusted Debian OS images, which might not be compatible with T-Pot. There is no cloud provider support provided of any kind.

<a name="firstrun"></a>
## First Run
The installation requires very little interaction, only a locale and keyboard setting have to be answered for the basic linux installation. While the system reboots maintain the active internet connection. The T-Pot installer will start and ask you for an installation type, password for the **tsec** user and credentials for a **web user**. Everything else will be configured automatically. All docker images and other componenents will be downloaded. Depending on your network connection and the chosen installation type, the installation may take some time. With 250Mbit down / 40Mbit up the installation is usually finished within 15-30 minutes.

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




<a name="updates"></a>
# Updates
For the ones of you who want to live on the bleeding edge of T-Pot development we introduced an update feature which will allow you to update all T-Pot relevant files to be up to date with the T-Pot master branch.
**If you made any relevant changes to the T-Pot relevant config files make sure to create a backup first.**

The Update script will:
 - **mercilessly** overwrite local changes to be in sync with the T-Pot master branch
 - upgrade the system to the packages available in Debian (Stable)
 - update all resources to be in-sync with the T-Pot master branch
 - ensure all T-Pot relevant system files will be patched / copied into the original T-Pot state
 - restore your custom ews.cfg and HPFEED settings from `/data/ews/conf`

You simply run the update script:
```
sudo su -
cd /opt/tpot/
./update.sh
```

**Despite all testing efforts please be reminded that updates sometimes may have unforeseen consequences. Please create a backup of the machine or the files with the most value to your work.**  

<a name="options"></a>
# Options
The system is designed to run without any interaction or maintenance and automatically contributes to the community.<br>
For some this may not be enough. So here some examples to further inspect the system and change configuration parameters.

<a name="ssh"></a>
## SSH and web access
By default, the SSH daemon allows access on **tcp/64295** with a user / password combination and prevents credential brute forcing attempts using `fail2ban`. This also counts for Admin UI (**tcp/64294**) and Web UI (**tcp/64297**) access.<br>

If you do not have a SSH client at hand and still want to access the machine via command line you can do so by accessing the Admin UI from `https://<your.ip>:64294`, enter

- user: **[tsec or user]** *you chose during one of the post install methods*
- pass: **[password]** *you chose during the installation*

You can also add two factor authentication to Cockpit just by running `2fa.sh` on the command line.

![Cockpit Terminal](doc/cockpit3.png)

<a name="heimdall"></a>
## T-Pot Landing Page 
Just open a web browser and connect to `https://<your.ip>:64297`, enter

- user: **[user]** *you chose during the installation*
- pass: **[password]** *you chose during the installation*

and the **Landing Page** will automagically load. Now just click on the tool / link you want to start.

![Dashbaord](doc/heimdall.png)

<a name="kibana"></a>
## Kibana Dashboard

![Dashbaord](doc/kibana.png)

<a name="tools"></a>
## Tools
The following web based tools are included to improve and ease up daily tasks.

![Cockpit Overview](doc/cockpit1.png)

![Cockpit Containers](doc/cockpit2.png)

![Cyberchef](doc/cyberchef.png)

![Spiderfoot](doc/spiderfoot.png)


<a name="maintenance"></a>
## Maintenance
T-Pot is designed to be low maintenance. Basically, there is nothing you have to do but let it run.

If you run into any problems, a reboot may fix it :bowtie:

If new versions of the components involved appear new docker images will be created and distributed. New images will be available from docker hub and downloaded automatically to T-Pot and activated accordingly.  

<a name="submission"></a>
## Community Data Submission
T-Pot is provided in order to make it accessible to all interested in honeypots. By default, the captured data is submitted to a community backend. This community backend uses the data to feed [Sicherheitstacho](https://sicherheitstacho.eu).
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
    image: "ghcr.io/telekom-security/ewsposter:2006"
    volumes:
     - /data:/data
     - /data/ews/conf/ews.ip:/opt/ewsposter/ews.ip
```
4. Start T-Pot services: `systemctl start tpot`

Data is submitted in a structured ews-format, a XML stucture. Hence, you can parse out the information that is relevant to you.

It is encouraged not to disable the data submission as it is the main purpose of the community approach - as you all know **sharing is caring** 😍

<a name="hpfeeds-optin"></a>
## Opt-In HPFEEDS Data Submission
As an Opt-In it is now possible to also share T-Pot data with 3rd party HPFEEDS brokers.  
If you want to share your T-Pot data you simply have to register an account with a 3rd party broker with its own benefits towards the community. You simply run `hpfeeds_optin.sh` which will ask for your credentials. It will automatically update `/opt/tpot/etc/tpot.yml` to deliver events to your desired broker.

The script can accept a config file as an argument, e.g. `./hpfeeds_optin.sh --conf=hpfeeds.cfg`

Your current config will also be stored in `/data/ews/conf/hpfeeds.cfg` where you can review or change it.  
Be sure to apply any changes by running `./hpfeeds_optin.sh --conf=/data/ews/conf/hpfeeds.cfg`.  
No worries: Your old config gets backed up in `/data/ews/conf/hpfeeds.cfg.old`

Of course you can also rerun the `hpfeeds_optin.sh` script to change and apply your settings interactively.

<a name="roadmap"></a>
# Roadmap
As with every development there is always room for improvements ...

Some features may be provided with updated docker images, others may require some hands on from your side.

You are always invited to participate in development on our [GitHub](https://github.com/telekom-security/tpotce) page.

<a name="faq"></a>
# FAQ
Please report any issues or questions on our [GitHub issue list](https://github.com/telekom-security/tpotce/issues), so the community can participate.

<a name="contact"></a>
# Contact
The software is provided **as is** in a Community Edition format. T-Pot is designed to run out of the box and with zero maintenance involved. <br>
We hope you understand that we cannot provide support on an individual basis. We will try to address questions, bugs and problems on our [GitHub issue list](https://github.com/telekom-security/tpotce/issues).

<a name="licenses"></a>
# Licenses
The software that T-Pot is built on uses the following licenses.
<br>GPLv2: [conpot](https://github.com/mushorg/conpot/blob/master/LICENSE.txt), [dionaea](https://github.com/DinoTools/dionaea/blob/master/LICENSE), [honeytrap](https://github.com/armedpot/honeytrap/blob/master/LICENSE), [suricata](http://suricata-ids.org/about/open-source/)
<br>GPLv3: [adbhoney](https://github.com/huuck/ADBHoney), [elasticpot](https://gitlab.com/bontchev/elasticpot/-/blob/master/LICENSE), [ewsposter](https://github.com/telekom-security/ews/), [log4pot](https://github.com/thomaspatzke/Log4Pot/blob/master/LICENSE), [fatt](https://github.com/0x4D31/fatt/blob/master/LICENSE), [heralding](https://github.com/johnnykv/heralding/blob/master/LICENSE.txt), [ipphoney](https://gitlab.com/bontchev/ipphoney/-/blob/master/LICENSE), [redishoneypot](https://github.com/cypwnpwnsocute/RedisHoneyPot/blob/main/LICENSE), [sentrypeer](https://github.com/SentryPeer/SentryPeer/blob/main/LICENSE.GPL-3.0-only), [snare](https://github.com/mushorg/snare/blob/master/LICENSE), [tanner](https://github.com/mushorg/snare/blob/master/LICENSE)
<br>Apache 2 License: [cyberchef](https://github.com/gchq/CyberChef/blob/master/LICENSE), [dicompot](https://github.com/nsmfoo/dicompot/blob/master/LICENSE), [elasticsearch](https://github.com/elasticsearch/elasticsearch/blob/master/LICENSE.txt), [logstash](https://github.com/elasticsearch/logstash/blob/master/LICENSE), [kibana](https://github.com/elasticsearch/kibana/blob/master/LICENSE.md), [docker](https://github.com/docker/docker/blob/master/LICENSE)
<br>MIT license: [ciscoasa](https://github.com/Cymmetria/ciscoasa_honeypot/blob/master/LICENSE), [ddospot](https://github.com/aelth/ddospot/blob/master/LICENSE), [elasticvue](https://github.com/cars10/elasticvue/blob/master/LICENSE), [glutton](https://github.com/mushorg/glutton/blob/master/LICENSE), [hellpot](https://github.com/yunginnanet/HellPot/blob/master/LICENSE), [maltrail](https://github.com/stamparm/maltrail/blob/master/LICENSE)
<br> Unlicense: [endlessh](https://github.com/skeeto/endlessh/blob/master/UNLICENSE)
<br> Other: [citrixhoneypot](https://github.com/MalwareTech/CitrixHoneypot#licencing-agreement-malwaretech-public-licence), [cowrie](https://github.com/micheloosterhof/cowrie/blob/master/LICENSE.md), [mailoney](https://github.com/awhitehatter/mailoney), [Debian licensing](https://www.debian.org/legal/licenses/), [Elastic License](https://www.elastic.co/licensing/elastic-license)
<br> AGPL-3.0: [honeypots](https://github.com/qeeqbox/honeypots/blob/main/LICENSE)


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
* [ddospot](https://github.com/aelth/ddospot/graphs/contributors)
* [debian](http://www.debian.org/)
* [dicompot](https://github.com/nsmfoo/dicompot/graphs/contributors)
* [dionaea](https://github.com/DinoTools/dionaea/graphs/contributors)
* [docker](https://github.com/docker/docker/graphs/contributors)
* [elasticpot](https://gitlab.com/bontchev/elasticpot/-/project_members)
* [elasticsearch](https://github.com/elastic/elasticsearch/graphs/contributors)
* [elasticvue](https://github.com/cars10/elasticvue/graphs/contributors)
* [endlessh](https://github.com/skeeto/endlessh/graphs/contributors)
* [ewsposter](https://github.com/armedpot/ewsposter/graphs/contributors)
* [fatt](https://github.com/0x4D31/fatt/graphs/contributors)
* [glutton](https://github.com/mushorg/glutton/graphs/contributors)
* [hellpot](https://github.com/yunginnanet/HellPot/graphs/contributors)
* [heralding](https://github.com/johnnykv/heralding/graphs/contributors)
* [honeypots](https://github.com/qeeqbox/honeypots/graphs/contributors)
* [honeytrap](https://github.com/armedpot/honeytrap/graphs/contributors)
* [ipphoney](https://gitlab.com/bontchev/ipphoney/-/project_members)
* [kibana](https://github.com/elastic/kibana/graphs/contributors)
* [logstash](https://github.com/elastic/logstash/graphs/contributors)
* [log4pot](https://github.com/thomaspatzke/Log4Pot/graphs/contributors)
* [mailoney](https://github.com/awhitehatter/mailoney)
* [maltrail](https://github.com/stamparm/maltrail/graphs/contributors)
* [medpot](https://github.com/schmalle/medpot/graphs/contributors)
* [p0f](http://lcamtuf.coredump.cx/p0f3/)
* [redishoneypot](https://github.com/cypwnpwnsocute/RedisHoneyPot/graphs/contributors)
* [sentrypeer](https://github.com/SentryPeer/SentryPeer/graphs/contributors),
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
A new version of T-Pot is released about every 6-12 months, development has shifted more and more towards rolling releases and the usage of `/opt/tpot/update.sh`.

<a name="testimonial"></a>
# Testimonials
One of the greatest feedback we have gotten so far is by one of the Conpot developers:<br>
***"[...] I highly recommend T-Pot which is ... it's not exactly a swiss army knife .. it's more like a swiss army soldier, equipped with a swiss army knife. Inside a tank. A swiss tank. [...]"***<br>
And from @robcowart (creator of [ElastiFlow](https://github.com/robcowart/elastiflow)):<br>
***"#TPot is one of the most well put together turnkey honeypot solutions. It is a must-have for anyone wanting to analyze and understand the behavior of malicious actors and the threat they pose to your organization."***
