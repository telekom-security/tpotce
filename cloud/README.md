# Ansible T-Pot Deployment on Open Telekom Cloud

Here you can find a ready-to-use solution for your automated T-Pot deployment using [Ansible](https://www.ansible.com/).  
It consists of multiple Ansible Playbooks, which can be reused across all Cloud Providers (like AWS, Azure, Digital Ocean).  
This example showcases the deployment on our own Public Cloud Offering [Open Telekom Cloud](https://open-telekom-cloud.com/en).

# Table of contents
- [Installation of Ansible Master](#installation)
  - [Packages](#packages)
  - [Agent Forwarding](#agent-forwarding)
- [Preparations in Open Telekom Cloud Console](#preparation)
  - [Create new project](#project)
  - [Create API user](#api-user)
  - [Import Key Pair](#key-pair)
  - [Create VPC, Subnet and Security Group](#vpc-subnet-securitygroup)
- [Clone Git Repository](#clone-git)
- [Settings and recommended values](#settings)
  - [Configure `.otc_env.sh`](#otc-env)
  - [Configure `.ecs_settings.sh`](#ecs-settings)
  - [Configure `tpot.conf.dist`](#tpot-conf)
  - [Optional: Custom `ews.cfg`](#ews-cfg)
  - [Optional: Configure `.hpfeeds_settings.sh`](#hpfeeds)

<a name="installation"></a>
# Installation of Ansible Master
You can either run the deploy script locally on your Linux or MacOS machine or you can use an ECS (Elastic Cloud Server) on Open Telekom Cloud, which I did.  
I used Ubuntu 18.04 for my Ansible Master Server, but other OSes are fine too.  
Ansible works over the SSH Port, so you don't have to add any special rules to you Security Group.

<a name="packages"></a>
## Packages
At first we need to add the repository and install Ansible:  
`sudo apt-add-repository --yes --update ppa:ansible/ansible`  
`sudo apt install ansible`

Also we need **pwegen** (for creating T-Pot names) and **jq** (a JSON processor):  
`sudo apt install pwgen jq`

<a name="agent-forwarding"></a>
## Agent Forwarding
Agent forwarding must be enabled in order to let Ansible do its work.  
- On Linux or MacOS:  
  - Create or edit `~/.ssh/config`
  - If you execute the script remotely on your Ansible Master Server:
    ```
    Host ANSIBLE_MASTER_IP
    ForwardAgent yes
    ```
  - If you execute the script locally, enable it for all Hosts, as this includes newly generated T-Pots:
    ```
    Host *
    ForwardAgent yes
    ```
- On Windows using Putty:  
![Putty Agent Forwarding](cloud/doc/putty_agent_forwarding.png)

<a name="preparation"></a>
# Preparations in Open Telekom Cloud Console
Before we can start deploying, we have to prepare the Open Telekom Cloud Tennant.  
For that, go to the Web [Console](https://auth.otc.t-systems.com/authui/login) and log in with an admin user.

<a name="project"></a>
## Create new project
I strongly advice you, to create a separate project for the T-Pots in your tennant.  
In my case I named it `tpot`.

<a name="api-user"></a>
## Create API user
The next step is to create a new user account, which is restricted to the project.  
This ensures that the API access is limited to that project.

<a name="key-pair"></a>
## Import Key Pair
Log in with the newly created user account and import your SSH public key.

<a name="vpc-subnet-securitygroup"></a>
## Create VPC, Subnet and Security Group
- VPC and Subnet:  

- Security Group:  

<a name="clone-git"></a>
# Clone Git Repository
Clone the `tpotce` repository to your Ansible Master:  
`git clone https://github.com/dtag-dev-sec/tpotce.git`  
All Ansible and automatic deployment related files are located in the `cloud` folder.

<a name="settings"></a>
# Settings and recommended values
You can configure all

<a name="otc-env"></a>
## Configure `.otc_env.sh`
Enter your API user credentials here (username, password, tennant-ID, project name):  
```
export OS_USERNAME=your_api_user
export OS_PASSWORD=your_password
export OS_USER_DOMAIN_NAME=OTC-EU-DE-000000000010000XXXXX
export OS_PROJECT_NAME=eu-de_your_project
export OS_AUTH_URL=https://iam.eu-de.otc.t-systems.com/v3
```

<a name="ecs-settings"></a>
## Configure `.ecs_settings.sh`
Here you can customize your Elastic Cloud Server (ECS):
  - Password for the user `linux` (you should change that)
  - For using a custom `ews.cfg` set to `true`; See here: [Optional: Custom `ews.cfg`](#ews-cfg)
  - Change the instance type (flavor) of the ECS.  
    `s2.medium.8` corresponds to 1 vCPU and 8GB of RAM and is the minimum required flavor.  
    A full list of flavors can be found [here](https://docs.otc.t-systems.com/en-us/usermanual/ecs/en-us_topic_0035470096.html).
  - Change the OS (For T-Pots we need Debian 9)
  - Specify the VPC, Subnet, Security Group and Key Pair you created before
  - Additionally you can change the disk size
  - You can choose from multiple Availibility Zones (AZ). For reference see [here](https://docs.otc.t-systems.com/en-us/endpoint/index.html)

```
# Set password for user linux
linuxpass=LiNuXuSeRPaSs#

# Custom EWS config
custom_ews=false

# Set ECS related stuff
instance=s2.medium.8
imagename=Standard_Debian_9_latest
subnet=your-subnet
vpcname=your-vpc
secgroup=your-sg
keyname=your-KeyPair
disksize=128
az=eu-de-03
```

<a name="tpot-conf"></a>
## Configure `tpot.conf.dist`

<a name="ews-cfg"></a>
## Optional: Custom `ews.cfg` 
- custom_ews in .ecs_settings.sh; contact, username, token

<a name="hpfeedss"></a>
## Optional: Configure `.hpfeeds_settings.sh`
