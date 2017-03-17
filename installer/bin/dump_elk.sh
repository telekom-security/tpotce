#/bin/bash
myDATE=$(date +%Y%m%d%H%M)
myINDICES=$(curl -s -XGET 'http://127.0.0.1:64298/_cat/indices/' | grep logstash | awk '{ print $3 }' | sort | grep -v 1970)
myES="http://127.0.0.1:64298/"
myCOL1="[0;34m"
myCOL0="[0;0m"
mkdir $myDATE
for i in $myINDICES;
  do
    echo $myCOL1"### Now dumping: "$i $myCOL0
    elasticdump --input=$myES$i --output=$myDATE"/"$i --limit 7500
    echo $myCOL1"### Now compressing: $myDATE/$i" $myCOL0
    gzip -f $myDATE"/"$i
  done;
echo $myCOL1"### Now building tar archive: es_dump_"$myDATE".tgz" $myCOL0
cd $myDATE
tar cvfz es_dump_$myDATE.tgz *
mv es_dump_$myDATE.tgz ..
cd ..
rm -rf $myDATE
echo $myCOL1"### Done."$myCOL0
