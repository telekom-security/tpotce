# Docker Smoke Tests

This directory contains post-build smoke tests for T-Pot Docker images.

The tests expect images to exist locally. They do not build images, and they do
not touch production `data/` or `data_backup/` paths.

## Usage

```bash
./docker/_tests/run.sh --list
./docker/_tests/run.sh
./docker/_tests/run.sh adbhoney
./docker/_tests/run.sh ciscoasa
./docker/_tests/run.sh citrixhoneypot
./docker/_tests/run.sh conpot
./docker/_tests/run.sh cowrie
./docker/_tests/run.sh ddospot
./docker/_tests/run.sh dionaea
./docker/_tests/run.sh dicompot
./docker/_tests/run.sh elasticpot
./docker/_tests/run.sh endlessh
./docker/_tests/run.sh fatt
./docker/_tests/run.sh go-pot
./docker/_tests/run.sh h0neytr4p
./docker/_tests/run.sh hellpot
./docker/_tests/run.sh heralding
./docker/_tests/run.sh honeyaml
./docker/_tests/run.sh honeypots
./docker/_tests/run.sh log4pot
./docker/_tests/run.sh mailoney
./docker/_tests/run.sh medpot
./docker/_tests/run.sh miniprint
./docker/_tests/run.sh p0f
./docker/_tests/run.sh redishoneypot
./docker/_tests/run.sh rdphoneypot
./docker/_tests/run.sh sentrypeer
./docker/_tests/run.sh suricata
```

Common options:

```bash
./docker/_tests/run.sh --timeout 45 --bind-ip 127.0.0.1
./docker/_tests/run.sh --keep-artifacts adbhoney
```

The runner checks common and test-specific dependencies before starting
containers, and fails early with a list of missing tools.

Individual tests can also be run directly:

```bash
./docker/_tests/tests/adbhoney.sh
./docker/_tests/tests/adbhoney.sh --image dtagdevsec/adbhoney:24.04 --host-port 15555
./docker/_tests/tests/ciscoasa.sh --https-port 18443 --ike-port 15000
./docker/_tests/tests/citrixhoneypot.sh --https-port 1443
./docker/_tests/tests/conpot.sh --guardian-ast-port 11001 --ipmi-port 1623
./docker/_tests/tests/cowrie.sh
./docker/_tests/tests/cowrie.sh --ssh-port 2222 --telnet-port 2323
./docker/_tests/tests/cowrie.sh --persona debian-bookworm-vuln
./docker/_tests/tests/cowrie.sh --persona openwrt-1806
./docker/_tests/tests/ddospot.sh
./docker/_tests/tests/ddospot.sh --dns-port 1053 --ntp-port 1123 --ssdp-port 19000
./docker/_tests/tests/dionaea.sh
./docker/_tests/tests/dionaea.sh --ftp-port 2121
./docker/_tests/tests/dicompot.sh
./docker/_tests/tests/dicompot.sh --host-port 11112
./docker/_tests/tests/elasticpot.sh
./docker/_tests/tests/elasticpot.sh --http-port 19200
./docker/_tests/tests/endlessh.sh
./docker/_tests/tests/endlessh.sh --ssh-port 2222
./docker/_tests/tests/fatt.sh
./docker/_tests/tests/go-pot.sh
./docker/_tests/tests/go-pot.sh --http-port 18080
./docker/_tests/tests/h0neytr4p.sh
./docker/_tests/tests/h0neytr4p.sh --http-port 18080 --https-port 18443
./docker/_tests/tests/hellpot.sh
./docker/_tests/tests/hellpot.sh --http-port 18080
./docker/_tests/tests/heralding.sh
./docker/_tests/tests/heralding.sh --image dtagdevsec/heralding:24.04.1
./docker/_tests/tests/honeyaml.sh
./docker/_tests/tests/honeyaml.sh --image dtagdevsec/honeyaml:24.04.1
./docker/_tests/tests/honeypots.sh
./docker/_tests/tests/honeypots.sh --image dtagdevsec/honeypots:24.04.1
./docker/_tests/tests/log4pot.sh
./docker/_tests/tests/log4pot.sh --image log4pot:alpine-check --http-port 18080
./docker/_tests/tests/mailoney.sh
./docker/_tests/tests/mailoney.sh --image mailoney:test --smtp-port 10025
./docker/_tests/tests/medpot.sh
./docker/_tests/tests/medpot.sh --image dtagdevsec/medpot:24.04.1 --host-port 12575
./docker/_tests/tests/miniprint.sh
./docker/_tests/tests/miniprint.sh --image dtagdevsec/miniprint:24.04 --raw-port 19100
./docker/_tests/tests/p0f.sh
./docker/_tests/tests/p0f.sh --image dtagdevsec/p0f:24.04.1
./docker/_tests/tests/redishoneypot.sh
./docker/_tests/tests/redishoneypot.sh --redis-port 16379
./docker/_tests/tests/rdphoneypot.sh
./docker/_tests/tests/rdphoneypot.sh --rdp-port 13389
./docker/_tests/tests/sentrypeer.sh
./docker/_tests/tests/sentrypeer.sh --tcp-port 15060 --udp-port 15060
./docker/_tests/tests/suricata.sh
```

The Dionaea test maps the tested service ports to temporary loopback ports,
mounts a temporary config without `services/sip.yaml`, and skips the SIP ports
`5060/tcp`, `5060/udp`, and `5061/tcp`. The `--ftp-port` option pins only the
host-side FTP control port; every other tested Dionaea port remains dynamically
assigned.

The Dicompot test additionally requires DCMTK client tools on the host:
`echoscu`, `getscu`, `dcmdump`, and either `setscu` or `storescu`.

The p0f test generates HTTP traffic inside an isolated Docker network and
verifies that p0f writes matching `syn` and `http request` JSON events.

The RDPHoneypot test also verifies that `server.pem` is written to the
persistent cert volume and remains unchanged after a container restart.

The SentryPeer test sends SIP OPTIONS probes over TCP and UDP, verifies SIP
responses, and checks matching JSON events in `sentrypeer.json`.

The Suricata test replays a generated HTTP PCAP and verifies a matching HTTP
event in `eve.json` plus the Suricata runtime log.

## Conventions

- Put one executable test script per honeypot in `tests/<service>.sh`.
- Source `lib/common.sh` for Docker, Compose, cleanup, and artifact helpers.
- Use temporary directories under `/tmp` for logs and downloads.
- Bind host ports to loopback by default and prefer dynamic host ports.
- Fail with a clear image build hint when the target image is missing.
