#!/bin/bash

# Set vars
myDATE=$(date +%Y%m%d%H%M)
myPATH=$PWD
myELKPATH="data/elk/data"

# Backup ES
cd $HOME/tpotce

echo "### Now backing up Elasticsearch folders ..."
tar cvfz $myPATH"/elkall_"$myDATE".tgz" $myELKPATH

cd $myPATH
