#!/bin/bash

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
    echo "***********************************************"
    echo "*****        SSH TO TARGET: "
    echo "*****        ssh linux@$PUBIP -p 64295"
    echo "***********************************************"

else

    echo "ECS creation unsuccessful. Aborting..."
    echo "Hint: Check your EIP or ECS quotas as these limits are a common error."
    echo "For further output, comment out '2> /dev/null' in the ECS creation command."

fi
