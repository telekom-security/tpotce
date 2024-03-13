#!/usr/bin/env bash
docker run -v $PWD:/data --entrypoint bash -it -u $(id -u):$(id -g) dtagdevsec/tpotinit:alpha "/opt/tpot/bin/genuser.sh"
