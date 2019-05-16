#!/bin/bash

# Check if required packages are installed
if ! hash ansible 2>/dev/null; then
    echo "### Package 'ansible' is missing. Please install it with:"
    echo "    sudo apt-add-repository --yes --update ppa:ansible/ansible"
    echo "    sudo apt install ansible"
    exit 1
fi

if ! hash pwgen 2>/dev/null; then
    echo "### Package 'pwgen' is missing. Please install it with:"
    echo "    sudo apt install pwgen"
    exit 1
fi

if ! hash jq 2>/dev/null; then
    echo "### Package 'jq' is missing. Please install it with:"
    echo "    sudo apt install jq"
    exit 1
fi

# Check for Agent Forwarding
if ! printenv | grep SSH_AUTH_SOCK > /dev/null; then
    echo "### Agent forwarding seems to be disabled."
    echo "### In order to let Ansible do its work, please enable it."
    exit 1
fi

# Import ECS settings
source .ecs_settings.sh

# Import OTC authentication credentials
source .otc_env.sh

# Password is later used by Ansible
export LINUX_PASS=$linuxpass

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
2> otc_tools.log

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

    if grep '401 Unauthorized' otc_tools.log > /dev/null; then
        echo "### API username or password is incorrect"
    elif grep 'Flavor' otc_tools.log > /dev/null; then
        echo "### Specified ECS Flavor not found"
    elif grep 'No image found by name' otc_tools.log > /dev/null; then
        echo "### Specified Image not found"
    elif grep 'No subnet found by name' otc_tools.log > /dev/null; then
        echo "### Specified Subnet not found"
    elif grep 'No VPC found by name' otc_tools.log > /dev/null; then
        echo "### Specified VPC not found"
    elif grep 'No security-group found by name' otc_tools.log > /dev/null; then
        echo "### Specified Security Group not found"
    elif grep 'Invalid key_name provided' otc_tools.log > /dev/null; then
        echo "### Specified Key Pair not found"
    elif grep 'availability_zone' otc_tools.log > /dev/null; then
        echo "### Specified Availability Zone not found"
    elif grep 'quota' otc_tools.log > /dev/null; then
        echo "### Quota exceeded. Please check your available quotas online"
        echo "### You can either delete unused resources or apply for a higher quota"
    fi

    echo "### ECS creation unsuccessful. Aborting..."

fi
