# T-Pot Image Creator (Alpha - not ready for production!)

This repository contains the necessary files to create the **[T-Pot community honeypot](http://dtag-dev-sec.github.io/)**  ISO image.
The image can then be used to install T-Pot on a physical or virtual machine.

### Image Creation
**Requirements to create the ISO image:**
- Ubuntu 14.04.3 or newer as host system (others *may* work, but remain untested)
- 4GB of free memory  
- 32GB of free storage
- A working internet connection

**How to create the ISO image:**

1. Clone the repository and enter it.

        git clone https://github.com/dtag-dev-sec/tpotce.git
        cd tpotce

2. Invoke the script that builds the ISO image.
The script will download and install dependecies necessary to build the image on the invoking machine. It will further download the ubuntu base image (~600MB) which T-Pot is based on.

        sudo ./makeiso.sh

After a successful build, you will find the ISO image `tpot.iso` in your directory.

### T-Pot Installation (Cowrie, Dionaea, ElasticPot, Glastopf, Honeytrap, ELK, Suricata+P0f)
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:
- 4 GB RAM (6-8 GB recommended)
- 64 GB disk (128 GB SSD recommended)
- Network via DHCP
- A working internet connection

### Sensor Installation (Cowrie, Dionaea, ElasticPot, Glastopf, Honeytrap - only available thru ISO Creator)
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:
- 3 GB RAM (4-6 GB recommended) 
- 64 GB disk (64 GB SSD recommended)
- Network via DHCP
- A working internet connection

### Industrial Installation (ConPot, eMobility, ELK, Suricata+P0f - only available thru ISO Creator)
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:
- 4 GB RAM (8 GB recommended)
- 64 GB disk (128 GB SSD recommended)
- Network via DHCP
- A working internet connection

### Everything Installation (Everything)
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:
- 8 GB RAM
- 128 GB disk or larger (128 GB SSD or larger recommended)
- Network via DHCP
- A working internet connection

The installation requires very little interaction. Most things should be configured automatically. The system will reboot a couple of times. Make sure it can access the internet as it needs to download the dockerized honeypot components. Depending on your network connection, the installation may take some time.
Once the installation is finished, the system will automatically reboot and you will be presented with a login screen. The user credentials for the first login are:
- user: tsec
- pass: tsec

You will need to set a new password after first login.

All honeypot services are started automatically.  

For further information and a more in depth installation instruction, visit [T-Pot's website](http://dtag-dev-sec.github.io/).
