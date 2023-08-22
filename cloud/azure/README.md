
# Azure T-Pot

The following deployment template will deploy a Standard T-Pot server on a Azure VM on a Network\Subnet of your choosing. [Click here to learn more on T-Pot](https://github.com/telekom-security/tpotce)

[![Deploy To Azure](https://raw.githubusercontent.com/Azure/azure-quickstart-templates/master/1-CONTRIBUTION-GUIDE/images/deploytoazure.svg?sanitize=true)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Ftelekom-security%2Ftpotce%2Fmaster%2Fcloud%2Fazure%2Fazuredeploy.json)
[![Deploy To Azure US Gov](https://raw.githubusercontent.com/Azure/azure-quickstart-templates/master/1-CONTRIBUTION-GUIDE/images/deploytoazuregov.svg?sanitize=true)](https://portal.azure.us/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Ftelekom-security%2Ftpotce%2Fmaster%2Fcloud%2Fazure%2Fazuredeploy.json)
[![Visualize](https://raw.githubusercontent.com/Azure/azure-quickstart-templates/master/1-CONTRIBUTION-GUIDE/images/visualizebutton.svg?sanitize=true)](http://armviz.io/#/?load=https%3A%2F%2Fraw.githubusercontent.com%2Ftelekom-security%2Ftpotce%2Fmaster%2Fcloud%2Fazure%2Fazuredeploy.json)

## Install Instructions

 1. Update the VM Name to reflect your naming convention and taxonomy.  
 2. Place you Azure Virtual Network Resource Id *(Recommendation of
    placement depending on goal, you may want to place    in Hub Virtual
    Network to detect activity from on-premise or other    virtual
    network spokes. You can also place in DMZ or isolated in a    unique
    virtual network exposed to direct internet.)*
  3. My Connection IP of a public ip address you are coming from to use dashboards and manage.
  4. Cloud Init B64 Encoded write your cloud init yaml contents and base 64 encode them into this string parameter.

Cloud-Init Yaml Example before B64 Encoding:

    packages:
      - git
    
    runcmd:
      - curl -sS --retry 5 https://github.com
      - git clone https://github.com/telekom-security/tpotce /root/tpot
      - /root/tpot/iso/installer/install.sh --type=auto --conf=/root/tpot.conf
      - rm /root/tpot.conf
      - /sbin/shutdown -r now
    
    password: w3b$ecrets2!
    chpasswd:
      expire: false
    
    write_files:
      - content: |
          # tpot configuration file
          myCONF_TPOT_FLAVOR='STANDARD'
          myCONF_WEB_USER='webuser'
          myCONF_WEB_PW='w3b$ecrets2!'
        owner: root:root
        path: /root/tpot.conf
        permissions: '0600'

Be sure to copy and update values like: 

 - password: 
 - myCONF_TPOT_FLAVOR= (Different flavors as follows: [STANDARD,
   HIVE, HIVE_SENSOR, INDUSTRIAL, LOG4J, MEDICAL, MINI, SENSOR]
   **Recommend deploying STANDARD** if you are exploring first time)
 - myCONF_WEB_USER=
 - myCONF_WEB_PW=

Once you update the cloud init yaml file locally then base 64 encode and paste this string to in the securestring parameter.

B64 Example: 

    I2Nsb3VkLWNvbmZpZwp0aW1lem9uZTogVVMvRWFzdGVybgoKcGFja2FnZXM6CiAgLSBnaXQKCnJ1bmNtZDoKICAtIGN1cmwgLXNTIC0tcmV0cnkgNSBodHRwczovL2dpdGh1Yi5jb20KICAtIGdpdCBjbG9uZSBodHRwczovL2dpdGh1Yi5jb20vdGVsZWtvbS1zZWN1cml0eS90cG90Y2UgL3Jvb3QvdHBvdAogIC0gL3Jvb3QvdHBvdC9pc28vaW5zdGFsbGVyL2luc3RhbGwuc2ggLS10eXBlPWF1dG8gLS1jb25mPS9yb290L3Rwb3QuY29uZgogIC0gcm0gL3Jvb3QvdHBvdC5jb25mCiAgLSAvc2Jpbi9zaHV0ZG93biAtciBub3cKCnBhc3N3b3JkOiB3M2IkZWNyZXRzMiEKY2hwYXNzd2Q6CiAgZXhwaXJlOiBmYWxzZQoKd3JpdGVfZmlsZXM6CiAgLSBjb250ZW50OiB8CiAgICAgICMgdHBvdCBjb25maWd1cmF0aW9uIGZpbGUKICAgICAgbXlDT05GX1RQT1RfRkxBVk9SPSdTVEFOREFSRCcKICAgICAgbXlDT05GX1dFQl9VU0VSPSd3ZWJ1c2VyJwogICAgICBteUNPTkZfV0VCX1BXPSd3M2IkZWNyZXRzMiEnCiAgICBvd25lcjogcm9vdDpyb290CiAgICBwYXRoOiAvcm9vdC90cG90LmNvbmYKICAgIHBlcm1pc3Npb25zOiAnMDYwMCc=

Click review and create, deployment of VM should take less than 5 minutes, however Cloud-Init will take some time, **typically 15 minutes** before T-Pot services are up and running.

## Post Install Instructions
Install **may take around 15 minutes** for services to come up. Check to make sure from your public IP you can connect to https://azurepuplicip:64297 you will be prompted for your username and password supplied in the B64 Cloud Init String you supplied for *myCONF_WEB_PW=* 

Review the [available honeypots architecture section](https://raw.githubusercontent.com/telekom-security/tpotce/master/doc/architecture.png) and [available ports](https://github.com/telekom-security/tpotce#required-ports) and poke a hole in the Network Security Group to expose the T-Pot to your on-premise network CIDR, or other Azure virtual network CIDRs, finally you can also expose a port to the public Internet for Threat Intelligence gathering.

## Network Security Group
Please study the rules carefully. You may need to make some additional rules or modifications based on your needs and considerations. As an example if this is for internal private ip range detections you may want to remove rules and place a higher priority DENY rule preventing all the T-Pot ports and services being exposed internally, and then place a few ALLOW rules to your on-premise private ip address CIDR, other Hub Private IPs, and some Spoke Private IPs.
![enter image description here](https://raw.githubusercontent.com/telekom-security/tpotce/master/cloud/azure/images/nsg.png)
