#!/bin/ash
sed -i "s/var hqLatLng = new L.LatLng(52.3058, 4.932);/var hqLatLng = new L.LatLng($MY_EXTIP_LAT, $MY_EXTIP_LONG);/g" /opt/geoip-attack-map/static/map.js
