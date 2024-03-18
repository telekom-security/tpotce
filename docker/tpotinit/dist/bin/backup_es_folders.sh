#!/bin/bash

if [ "$1" == "" ] || [ "$1" != "all" ] && [ "$1" != "base" ];
  then
    echo "Usage: backup_es_folders [all, base]"
    echo "       all  = backup all ES folder"
    echo "       base = backup only Kibana index".
    echo
    exit
fi

# Backup all ES relevant folders
# Make sure ES is available
myES="http://127.0.0.1:64298/"
myESSTATUS=$(curl -s -XGET ''$myES'_cluster/health' | jq '.' | grep -c green)
if ! [ "$myESSTATUS" = "1" ]
  then
    echo "### Elasticsearch is not available."
    exit
  else
    echo "### Elasticsearch is available, now continuing."
    echo
fi

# Set vars
myDATE=$(date +%Y%m%d%H%M)
myPATH=$PWD
myELKPATH="data/elk/data"
myKIBANAINDEXNAMES=$(curl -s -XGET ''$myES'_cat/indices/.kibana_*?v&s=index&h=uuid' | tail -n +2)
#echo $myKIBANAINDEXNAMES
for i in $myKIBANAINDEXNAMES;
  do
    myKIBANAINDEXPATHS="$myKIBANAINDEXPATHS $myELKPATH/indices/$i"
done

# Backup DB in 2 flavors
cd $HOME/tpotce

echo "### Now backing up Elasticsearch folders ..."
if [ "$1" == "all" ];
  then
    tar cvfz $myPATH"/elkall_"$myDATE".tgz" $myELKPATH
elif [ "$1" == "base" ];
  then
    tar cvfz $myPATH"/elkbase_"$myDATE".tgz" $myKIBANAINDEXPATHS
fi

cd $myPATH
