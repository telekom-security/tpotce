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

        git clone git@github.com:dtag-dev-sec/tpotce.git
        cd tpotce

2. Invoke the script that builds the ISO image.
The script will download and install dependecies necessary to build the image on the invoking machine. It will further download the ubuntu base image (~600MB) which T-Pot is based on.

        sudo ./makeiso.sh

After a successful build, you will find the ISO image `tpot.iso` in your directory.

###Installation
When installing the T-Pot ISO image, make sure the target system (physical/virtual) meets the following minimum requirements:
- 4 GB RAM
- 40 GB disk (64 GB SSD recommended)
- Network via DHCP
- A working internet connection

The installation requires very little interaction. Most things should be configured automatically. The system will reboot a couple of times. Make sure it can access the internet as it needs to download the dockerized honeypot components. Depending on your network connection, the installation may take some time.
Once the installation is finished, the system will automatically reboot and you will be presented with a login screen. The user credentials for the first login are:
- user: tsec
- pass: tsec

You will need to set a new password after first login.

All honeypot services are started automatically.  

For further information and a more in depth installation instruction, visit [T-Pot's website](http://dtag-dev-sec.github.io/).
