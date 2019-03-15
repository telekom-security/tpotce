#!/bin/bash

myTPOTYMLFILE="/opt/tpot/etc/tpot.yml"

echo "SISSDEN Delivery Opt-In for EWSPoster"
echo "-------------------------------------"
echo "By running this script you agree to share your data with https://sissden.eu and agree to the corresponding sharing terms."
echo
echo "Please provide the credentials you created at the SISSDEN portal ..."
read -p "Ident: " myIDENT
read -p "Secret: " mySECRET
echo
echo "Now stopping T-Pot ..."
systemctl stop tpot
echo "Adding your credentials ..."
sed -i.bak 's/EWS_HPFEEDS_ENABLE=false/EWS_HPFEEDS_ENABLE=true/g' "$myTPOTYMLFILE"
sed -i 's/EWS_HPFEEDS_HOST=host/EWS_HPFEEDS_HOST=hpfeeds.sissden.eu/g' "$myTPOTYMLFILE"
sed -i 's/EWS_HPFEEDS_PORT=port/EWS_HPFEEDS_PORT=10000/g' "$myTPOTYMLFILE"
sed -i 's/EWS_HPFEEDS_CHANNELS=channels/EWS_HPFEEDS_CHANNELS=t-pot.events/g' "$myTPOTYMLFILE"
sed -i "s/EWS_HPFEEDS_IDENT=user/EWS_HPFEEDS_IDENT=${myIDENT}/g" "$myTPOTYMLFILE"
sed -i "s/EWS_HPFEEDS_SECRET=secret/EWS_HPFEEDS_SECRET=${mySECRET}/g" "$myTPOTYMLFILE"
echo "Now starting T-Pot ..."
systemctl start tpot
echo "Done. On behalf of SISSDEN we thank you for sharing!"
echo
