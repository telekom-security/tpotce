#!/bin/bash

# Let's ensure normal operation on exit or if interrupted ...
function fuCLEANUP {
  exit 0
}
trap fuCLEANUP EXIT

# Download the latest EmergingThreats ruleset, replace rulebase and enable all rules
cd /tmp
wget --tries=2 --timeout=2 https://rules.emergingthreats.net/open/suricata-4.0/emerging.rules.tar.gz
tar xvfz emerging.rules.tar.gz -C /etc/suricata/
sed -i s/^#alert/alert/ /etc/suricata/rules/*.rules
