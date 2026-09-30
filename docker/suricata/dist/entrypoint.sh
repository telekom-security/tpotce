#!/bin/ash
set -eo pipefail

# Let's ensure normal operation on exit or if interrupted ...
function fuCLEANUP {
  exit 0
}
trap fuCLEANUP EXIT

### Vars
myOINKCODE="${OINKCODE}"
myRULESUPDATE="${SURICATA_RULES_UPDATE:-on}"
# Persisted in the data folder: the downloads of suricata-update (cache/, set in
# update.yaml), the rulesets from FROMURL (fromurl/), the last good merged
# ruleset (suricata.rules) and what it was built from (rules.id)
myCACHE="/var/lib/suricata/tpot"
myRULES="/var/lib/suricata/rules/suricata.rules"
myIMAGERULES="/var/lib/suricata/image/suricata.rules"
myFILTER="/tmp/capture.bpf"

# Check internet availability
function fuCHECKINET () {
mySITES=$1
error=0
for i in $mySITES;
  do
    if ! curl --connect-timeout 5 -Is "$i" > /dev/null 2>&1;
      then
        let error+=1
    fi;
  done;
  echo $error
}

# True if the file is missing, empty or 24h and older (same as the Listbot cache of Logstash)
function fuNEEDSUPDATE () {
  local myFILE=$1
  if [ ! -s "$myFILE" ] || [ -n "$(find "$myFILE" -mmin +1439)" ];
    then
      return 0
  fi
  return 1
}

# Age of a file in hours
function fuAGE () {
  echo $(( ( $(date +%s) - $(date -r "$1" +%s) ) / 3600 ))
}

# What a merged ruleset depends on, a change triggers a rebuild
function fuRULESID () {
  { echo "${myOINKCODE}|${FROMURL}"
    suricata -V 2>/dev/null
    cat /etc/suricata/enable.conf /etc/suricata/disable.conf /etc/suricata/modify.conf /etc/suricata/update.yaml 2>/dev/null
  } | sha256sum | cut -d " " -f1
}

# Enable or disable the ET Pro ruleset, a local change of the sources only
function fuSOURCES () {
  if [ "${myOINKCODE}" != "" ] && [ "${myOINKCODE}" != "OPEN" ];
    then
      suricata-update -q enable-source et/pro secret-code="${myOINKCODE}"
    else
      # suricata-update uses et/open ruleset by default if not configured
      rm -f /var/lib/suricata/update/sources/et-pro.yaml > /dev/null 2>&1
  fi
}

# Download the rulesets from FROMURL (tar.gz with a rules/ folder), replaces the
# previous ones only if all downloads succeed
function fuFROMURL () {
  [ -z "$FROMURL" ] && return 0
  local myNEW="${myCACHE}/fromurl.new"
  rm -rf "${myNEW}" && mkdir -p "${myNEW}"
  for URL in $(echo "$FROMURL" | tr '|' ' '); do
      if ! curl -fsS "$URL" -o /tmp/rules.tar.gz || ! tar -xzf /tmp/rules.tar.gz -C "${myNEW}";
        then
          echo "- Could not download the rules from FROMURL, keeping the previous ones."
          rm -rf "${myNEW}" /tmp/rules.tar.gz
          return 1
      fi
      rm -f /tmp/rules.tar.gz
  done
  rm -rf "${myCACHE}/fromurl" && mv "${myNEW}" "${myCACHE}/fromurl"
}

# Build the merged ruleset into the cache, $1 = online | offline
function fuBUILDRULES () {
  local myARGS="-q --no-test --no-reload -o /tmp/rules-new"
  [ "$1" == "offline" ] && myARGS="${myARGS} --offline"
  [ -d "${myCACHE}/fromurl/rules" ] && myARGS="${myARGS} --local ${myCACHE}/fromurl/rules"
  rm -rf /tmp/rules-new && mkdir -p /tmp/rules-new
  # shellcheck disable=SC2086 # the arguments are split on purpose
  if suricata-update ${myARGS} && [ -s /tmp/rules-new/suricata.rules ];
    then
      # A rebuild from the cached downloads keeps the age of the previous ruleset,
      # so the next start with internet access still updates it
      if [ "$1" == "offline" ] && [ -s "${myCACHE}/suricata.rules" ];
        then
          touch -r "${myCACHE}/suricata.rules" /tmp/rules-new/suricata.rules
      fi
      mv -f /tmp/rules-new/suricata.rules "${myCACHE}/suricata.rules.tmp"
      mv -f "${myCACHE}/suricata.rules.tmp" "${myCACHE}/suricata.rules"
      echo "${myRULESID}" > "${myCACHE}/rules.id"
      rm -rf /tmp/rules-new
      [ "$1" == "offline" ] && echo "- Rebuilt the rules from the cached downloads."
      return 0
  fi
  rm -rf /tmp/rules-new
  return 1
}

# The interface comes first: a wrong TPOT_CAPTURE_INTERFACE fails the start
# before the rules are touched, so a restart loop is cheap
if ! myIF="$(capture-if.sh)";
  then
    echo "- No capture interface, aborting."
    exit 1
fi

# The host part of the capture filter needs DNS, without it libpcap cannot
# compile the filter, the ports and broadcast / multicast are always excluded
myRESOLVED="yes"
for myHOST in sicherheitstacho.eu community.sicherheitstacho.eu listbot.sicherheitstacho.eu;
  do
    if ! nslookup "${myHOST}" > /dev/null 2>&1;
      then
        myRESOLVED="no"
    fi
done
if [ "${myRESOLVED}" == "yes" ];
  then
    { cat /etc/suricata/capture-filter-hosts.bpf | sed 's/$/ and/'; cat /etc/suricata/capture-filter.bpf; } > "${myFILTER}"
  else
    cp /etc/suricata/capture-filter.bpf "${myFILTER}"
fi

# Rules: the cache is updated once in 24h, or when OINKCODE, FROMURL or the rule
# settings change. Without internet access, or with SURICATA_RULES_UPDATE=off,
# the latest cached ruleset is used, the ruleset of the image only as last resort.
mkdir -p "${myCACHE}"
myRULESID="$(fuRULESID)"
myREBUILD="no"
if [ "$(cat "${myCACHE}/rules.id" 2>/dev/null)" != "${myRULESID}" ];
  then
    myREBUILD="yes"
fi
if ! fuNEEDSUPDATE "${myCACHE}/suricata.rules" && [ "${myREBUILD}" == "no" ];
  then
    echo "- Cached rules are current (< 24h), skipping the update."
  elif [ "${myRULESUPDATE}" != "on" ];
    then
      echo "- SURICATA_RULES_UPDATE=${myRULESUPDATE}, not downloading any rules."
      if [ "${myREBUILD}" == "yes" ] && [ -d "${myCACHE}/cache" ];
        then
          fuSOURCES
          fuBUILDRULES offline || echo "- Could not rebuild the rules from the cached downloads."
      fi
  elif [ "$(fuCHECKINET "rules.emergingthreatspro.com rules.emergingthreats.net")" == "0" ];
    then
      echo "- Updating rules."
      suricata-update -q update-sources || echo "- Could not update the source index, using the previous one."
      fuSOURCES
      fuFROMURL || true
      if ! fuBUILDRULES online;
        then
          echo "- Could not update the rules, trying the cached downloads."
          fuBUILDRULES offline || echo "- Could not rebuild the rules from the cached downloads."
      fi
  else
    echo "- Cannot reach the rule servers, no update."
    if [ "${myREBUILD}" == "yes" ] && [ -d "${myCACHE}/cache" ];
      then
        fuSOURCES
        fuBUILDRULES offline || echo "- Could not rebuild the rules from the cached downloads."
    fi
fi
if [ -s "${myCACHE}/suricata.rules" ];
  then
    cp -f "${myCACHE}/suricata.rules" "${myRULES}"
    echo "- Rules: cached ruleset, $(fuAGE "${myCACHE}/suricata.rules")h old"
  else
    cp -f "${myIMAGERULES}" "${myRULES}"
    echo "- Rules: ruleset of the image, $(fuAGE "${myIMAGERULES}")h old (no cached ruleset yet)"
fi

# Info
echo "- Capture filter: ${myFILTER} (hosts excluded: ${myRESOLVED})"
echo "- Interface: ${myIF}"

# Run Suricata
exec suricata -v -F "${myFILTER}" -i "${myIF}"
