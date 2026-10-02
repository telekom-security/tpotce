# Heralding 2.0 integration

Python 3.14 with a pinned Python/uv build and source commit
`0f9b48ba19e4c60ad1e9e9b0193e9057ed64a2af` from `t3chn0m4g3/heralding`.
The image runs as uid/gid 2000 with cap_net_bind_service, read-only rootfs and a writable
`/tmp/heralding` tmpfs. Logs remain in `/var/log/heralding`. No tests are shipped in the image.

## Local-only build

The source commit has not been pushed. Use the Heralding checkout's helper, which archives
the exact pinned commit into a temporary named build context and bypasses the remote fetch:

```sh
# In the Heralding checkout, with tpotce as a sibling checkout.
tools/validation/build_tpot.sh ../tpotce heralding:tpot-dev
../tpotce/docker/_tests/tests/heralding.sh --image heralding:tpot-dev
uv run python tools/validation/check_tpot.py ../tpotce
```

The underlying Docker command is:

```sh
docker build --build-context "heralding_source=$source_context" \
  -t heralding:tpot-dev docker/heralding
```

`source_context` must contain the archive in a `heralding/` subdirectory. The remote build
stage checks the exact commit and becomes usable once that commit exists at
`HERALDING_REPOSITORY`. No image, source branch or release was published for this change.
Production compose profiles retain their configured image tag. Build/tag locally before
starting a modified profile; the running T-Pot stack was not restarted.

## Configuration and free ports

Existing capability settings, banners and log paths are preserved. New capabilities are
configured internally; host mappings are added only when the same port/protocol is free
in that profile. Every existing competing honeypot retains its mapping.

| Profile | Additional Heralding host ports |
|---|---|
| root, standard, sensor, mobile | 389, 636, 990, 8883 TCP |
| mac_win | above plus 445 TCP |
| tarpit | above plus 445, 587, 1433, 1883, 6379 TCP; 5060 TCP+UDP |
| tpot_services catalogue | 636, 990, 8883 TCP (389 belongs to honeypots) |
| standalone docker/heralding compose | all new service ports, including 8080 |

SMB stays on Dionaea's 445 in standard/sensor/mobile. In tarpit, 8080 stays with go-pot.
The SOCKS5 smoke condition accepts the old and current authentication reply versions.
The obsolete requirements.txt override has been removed; uv.lock owns dependencies.

## Verified locally

- Docker build with the pinned local source archive.
- Existing 11-service Heralding smoke against the built T-Pot image, auth/session logs present.
- LDAP, LDAPS, FTPS and MQTTS credential capture using ldap3, ftplib and paho-mqtt.
- Free-port policy for every Heralding profile; `docker compose config --quiet` for all eight
  changed compose files.
- Installed package in site-packages, no tests in the image, uid 2000.
- Heralding's original and updated T-Pot fixtures start/stop in its standard-client suite.

The Heralding checkout's docs/TPOT.md and completion audit describe protocol limits and
manual FreeRDP/Hydra/smbclient/nmap/sipsak/Hashcat results.
