#!/bin/bash

# Check if required packages are installed
if ! hash ansible 2>/dev/null; then
    echo "Package 'ansible' is missing. Please install it with:"
    echo "    sudo apt-add-repository --yes --update ppa:ansible/ansible"
    echo "    sudo apt install ansible"
    exit 1
fi

if ! hash pwgen 2>/dev/null; then
    echo "Package 'pwgen' is missing. Please install it with:"
    echo "    sudo apt install pwgen"
    exit 1
fi

if ! hash jq 2>/dev/null; then
    echo "Package 'jq' is missing. Please install it with:"
    echo "    sudo apt install jq"
    exit 1
fi

# Check for Agent Forwarding
if ! printenv | grep SSH_AUTH_SOCK > /dev/null; then
    echo "Agent forwarding seems to be disabled."
    echo "In order to let Ansible do its work, please enable it."
    exit 1
fi

# Import ECS settings
source .ecs_settings.sh

# Import OTC authentication credentials
source .otc_env.sh

# Import HPFEED settings
source .hpfeeds_settings.sh

# Password is later used by Ansible
export LINUX_PASS=$linuxpass

# HPFEED settings are later used by Ansible
export myENABLE=$myENABLE
export myHOST=$myHOST
export myPORT=$myPORT
export myCHANNEL=$myCHANNEL
export myIDENT=$myIDENT
export mySECRET=$mySECRET
export myCERT=$myCERT
export myFORMAT=$myFORMAT

# Ignore ssh host keys as they are new anyway
export ANSIBLE_HOST_KEY_CHECKING=False

# Create hosts directory
mkdir -p hosts

# Create random ID
HPNAME=t-pot-otc-$(pwgen -ns 6 -1)

# Get otc-tools
echo "### Cloning otc-tools..."
git clone https://github.com/OpenTelekomCloud/otc-tools.git  2>/dev/null

# Create ECS via OTC API
echo "### Creating new ECS host via OTC API..."
./otc-tools/otc.sh ecs create \
    --instance-type       $instance\
    --instance-name       $HPNAME\
    --image-name          $imagename\
    --subnet-name         $subnet\
    --vpc-name            $vpcname\
    --security-group-name $secgroup\
    --admin-pass          $linuxpass\
    --key-name            $keyname\
    --public              true\
    --disksize            $disksize\
    --disktype            SATA\
    --az	          $az\
    --wait \
2> /dev/null

if [ $? -eq 0 ]; then

    if [ "$(uname)" == "Darwin" ]; then
        PUBIP=$(./otc-tools/otc.sh ecs list 2>/dev/null | grep $HPNAME|cut -d "," -f2 |cut -d "\"" -f 2)
    else
        PUBIP=$(./otc-tools/otc.sh ecs list 2>/dev/null | grep $HPNAME|cut -d " " -f17)
    fi

    echo "[TPOT]" > ./hosts/$HPNAME
    echo $PUBIP  HPNAME=$HPNAME>> ./hosts/$HPNAME
    echo "### NEW HOST $HPNAME ON IP $PUBIP"

    ansible-playbook -i ./hosts/$HPNAME ./ansible/install.yaml

    if [ $custom_ews = true ]; then

        ansible-playbook -i ./hosts/$HPNAME ./ansible/custom_ews.yaml

    fi

    ansible-playbook -i ./hosts/$HPNAME ./ansible/reboot.yaml

    echo "***********************************************"
    echo "*****        SSH TO TARGET: "
    echo "*****        ssh linux@$PUBIP -p 64295"
    echo "***********************************************"

else

    echo "ECS creation unsuccessful. Aborting..."
    echo "Hint: Check your EIP or ECS quotas as these limits are a common error."
    echo "For further output, comment out '2> /dev/null' in the ECS creation command."

fi
