<!-- Draft for T-Pot 24.04.2 - review and complete before the release -->
# Release Notes / Changelog
T-Pot 24.04.2 moves the Elastic Stack to 9.5 on the official Elastic images, makes updating a lot safer with backups you can actually restore, and adds new honeypots and NSM tooling.

## New Features
* **RDPHoneypot** a Remote Desktop honeypot for RDP connection and credential telemetry.
* **Restore Script** `restore.sh` puts a backup written by `update.sh` back, as a whole or per group (checkout, configuration, `data/`, Kibana objects and ILM policy).
* **Update Script** has been reworked:
  * Backups go to `~/tpot_backups`, rotate, are checked for space and hold what git cannot bring back; `--full` adds all of `data/`.
  * Your Kibana objects and the ILM policy are exported before T-Pot is stopped.
  * The installed edition is detected and restored, `-b <branch>` / `-r <repo>` allow testing branches and forks, `-s` starts T-Pot after the update.
  * Before pulling a new Elastic Stack the update checks the disk space and warns if the backup does not hold the Elasticsearch data.
* **Cowrie Personas** let Cowrie present itself as different systems.
* **Conpot** emulates five further ICS devices: a Beckhoff CX embedded PC (ADS, ADS discovery, OPC UA), a LOYTEC building automation server (KNXnet/IP, BACnet/IP), a WirelessHART gateway (HART-IP), an ICCP / TASE.2 endpoint and a Schneider Modicon M340 (Modbus/TCP), the IEC 104 RTU now also speaks DNP3. Serial numbers, MAC and IP addresses, host names and similar identifiers are generated once per installation (`data/conpot/identity`) instead of shipping the same values on every T-Pot.
* **Smoke Tests** for the honeypot images (`docker/_tests`) and an end-to-end test of the Attack Map pipeline (`attackmap_pipeline_test.sh`).
* **Listbot** translation maps are cached.
* **P0f** adds `os_family` (Android, Apple, Linux, Windows, BSD, Other or Scanner), `os_confidence` and `os_samples` to client SYNs; `os_confidence` and `os_samples` only come with fingerprints that have enough data in the public datasets, the others (i.e. BSDs, old Linux and Windows versions) get the family alone. The confidence is the share of that family among the flows p0f gives the same label in two public, labelled datasets (CESNET 2024, MUNI 2021, CC BY 4.0) and comes from the new `conf` field of `p0f.fp` (`docker/p0f/tools/build_os_confidence.py`). Scanners get no confidence, fuzzy matches only the family.
* **P0f** flags clients whose User-Agent does not fit their TCP stack: `http request` events carry `user_agent`, `ua_os` (the OS family the User-Agent names), `os_family` / `os_confidence` of the connection's SYN, `ua_os_mismatch` (i.e. a Windows User-Agent from a Linux stack; Linux and Android count as one) and `ua_dishonest` (the User-Agent does not fit the HTTP header order). The new Kibana dashboard **P0f** shows these signals with the OS families and scanners, >T-Pot shows the signal counts; the dashboards leave out mismatches against TCP fingerprints the datasets measured below an `os_confidence` of 0.9. P0f only sees plain-text HTTP and the first request of a connection.
* **P0f** reads pcap files offline and prints JSON: `docker run --rm -v "$PWD:/pcap:ro" dtagdevsec/p0f:24.04.2 -r /pcap/capture.pcap`, a BPF filter can follow.

## Updates
* **Elastic Stack** has been updated to 9.5.4 (from 8.16.1) and is now built on the official Elastic images.
* **Elasticsearch** no longer rejects events once a daily index reaches its field limit (now 3000), further new fields are not indexed then but stay in the document.
* **Kibana** now runs with a 1 GB Node.js heap (`KIBANA_HEAP_MB` in `docker/elk/kibana/Dockerfile`) within a `mem_limit` of 2 GB.
* **Attack Map** has been updated to 4.0.0.
* **Beelzebub** has been updated to 3.9.2 and is built from upstream again instead of the T-Pot fork. The log format, the dashboards and the SSH host key stay as they were, further services (Telnet, MCP, LDAP, SMB, MSSQL, MQTT, RDP, PostgreSQL, VNC, Redis, Memcached) are prepared and can be enabled in the compose file.
* **Conpot** has been updated to 1.0.0, pinned to a later commit of the master branch (asyncio, TOML templates, new event schema). All Conpot and upstream default strings of the deployed templates have been replaced. The pinned commit includes fixes contributed upstream: Guardian AST no longer keeps a core at 100% CPU after a client sent anything but a command and closed the connection (the reason for the former CPU health check, which has been removed), IPMI answers again to clients that come back from a new source port, and Kamstrup and ICCP no longer crash on binary or malformed input.
* **Cowrie** has been updated to 3.0.0, pinned to a later commit of the main branch.
* **Suricata** has been updated to 8.0.7 (Alpine 3.24 package). The rules are now cached in `data/suricata/rules` and updated once in 24 hours (`SURICATA_RULES_UPDATE=off` never downloads them), a failed update or a start without internet access uses the latest cached rules, and the capture filter keeps excluding the T-Pot ports without DNS.
* **P0f** fingerprints have been updated for Windows 10 and newer, Linux 4.19 and newer, Android, current macOS / iOS, IPv6 and the scanners masscan, zmap and SYNs without TCP options (Mirai, hping). In a replay of the public datasets the right OS family rises from 76 % to 94 % on the data the new signatures come from, on an independent dataset (MUNI 2019) it stays at 97 %. SYNs the T-Pot host sends itself are no longer logged, and VLAN tagged traffic (802.1Q, QinQ, i.e. on mirror ports) is fingerprinted again; so far p0f silently dropped every tagged packet. The Kibana visualization `P0f OS Distribution` shows `os_family` of client SYNs with `os_confidence` of 0.9 or more, plus scanners; events of earlier releases have no `os_family`.
* **NSM services** (Suricata, P0f, Glutton) capture on the interface of the route to the internet, so hosts with more than one default route no longer end up in a restart loop. `TPOT_CAPTURE_INTERFACE` in `.env` sets the interface manually.
* **Update Script** adds settings that are new in `env.example` to an existing `.env` and comments out those that are no longer used.
* **T-Pot Init** validates all settings of `.env` and reports every invalid one at once before T-Pot starts. Settings of a service are only checked if it runs in the active edition, `TPOT_CAPTURE_INTERFACE` and the Attack Map time zone are checked against the host.
* **Nginx** has been updated to 1.28.3 (Alpine 3.23 package).
* **Cyberchef** has been updated to 11.0.0.
* **Galah** has been updated to 1.1.1 and is built from upstream again instead of the T-Pot fork. The log format stays as it was, failed LLM responses now show up in the Galah dashboard, and all LLM settings (API key, temperature, GCP Vertex AI) can be set in `.env`.
* **Elasticvue** has been updated to 1.15.0.
* **EWSPoster** has been updated to 1.33, pinned to a commit of the master branch, and no longer stops on an empty honeypot log file (Python 3.13 and later).
* **Go-Pot** has been updated to 1.2.0-rc-7.
* **H0neytr4p** has been updated to 0.44.
* **Hellpot** has been updated to 0.60.
* **Honeyaml** builds again: its Rust dependencies have been updated and it is now a static binary on a scratch image (branch `tpot-24.04.2` of the T-Pot fork, upstream is no longer maintained).
* **IPPHoney** has been updated to 2.0.2.
* **Honeypots** without releases were updated to their latest pushed code, pinned to a commit.
* Docker images now use **Alpine 3.23** (Suricata 3.24), **Go 1.26** or **Scratch** wherever possible; **Log4Pot** moved from Ubuntu to Alpine.
* **Installer** supports unattended installations and has been tested with **Alma 10**, **Debian 13**, **Fedora 44**, **OpenSuse Tumbleweed**, **Rocky 10**, **RHEL 10** and **Ubuntu 26.04** (sudo-rs).
* **Persistence** cycles for logrotate are configurable through `TPOT_PERSISTENCE_CYCLES` in `.env`.
* **Beelzebub** and **Honeypots** log their status as text (i.e. `Stateless`, `failed`), it is now indexed as `status_text`. So far Elasticsearch could not index it in the numeric `status` field, some values ended up as `0` and with 24.04.1 part of these events were not indexed at all.
* **Fatt** has been removed, see [Breaking Changes](#fatt).
* **Spiderfoot** has been removed, see [Breaking Changes](#spiderfoot).
* Updates for `24.04.2` images will be provided continuously through Docker image updates.

## Breaking Changes
### Elastic Stack
- Elasticsearch and Kibana upgrade their data in `~/tpotce/data/elk` on the first start, ***this cannot be undone***. Run the update with `update.sh -y --full` or take a snapshot of the machine first, see [Elastic Stack Upgrades](README.md#elastic-stack-upgrades).
- If you use a `docker-compose.yml` of your own, raise the `mem_limit` of the `kibana` service to `2g`.
- The official Elastic images are based on UBI 9 and require a **x86-64-v2** capable CPU on x86 hosts, otherwise Elasticsearch, Kibana and Logstash stop with `Fatal glibc error: CPU does not support x86-64-v2`. Virtual machines using the legacy `kvm64` / `qemu64` CPU models are affected, e.g. Proxmox VE VMs created before 8.0 or QEMU without `-cpu`. Use the `host` CPU type or at least `x86-64-v2`.

### Update Script
- Updates are supported from **24.04.1** onwards. Installations of 24.04.0 need a fresh install.

### Conpot
- Conpot logs the event schema of Conpot 1.0: `protocol` replaces `data_type`, `session_id` replaces `id`, `event_time` and `session_time` replace `timestamp`, further protocol details are indexed as `conpot_data`. Import the Kibana objects of this release, the Conpot dashboard then shows the protocol from `protocol`; older events keep `data_type`.
- EWSPoster reads both formats, older EWSPoster images stop with an error on the new Conpot logs.
- If you use a `docker-compose.yml` of your own, add the volume `${TPOT_DATA_PATH}/conpot/identity:/var/lib/conpot` to the Conpot services and take the new services from `compose/standard.yml`.

### Dionaea
- The SIP service has been removed.

### Fatt
- Fatt has been removed, Suricata already logs its fingerprints (JA3 / JA3S / JA4, HASSH, RDP, HTTP, QUIC) and detects the protocols on every port, Fatt only looked at a fixed set of ports. The Dockerfile has moved to `docker/deprecated/fatt`.
- The Suricata dashboard now also shows SSH HASSH, RDP client names and HTTP URLs. Import the Kibana objects of this release to get it; the import does not delete the Fatt dashboard, remove it under Stack Management → Saved Objects (tag `Fatt`).
- `update.sh` removes the `fatt` service from a `docker-compose.yml` of your own, the previous file stays in the backup.
- The logs in `~/tpotce/data/fatt` are kept. If you no longer need them, remove the folder with `sudo rm -rf ~/tpotce/data/fatt`.

### Spiderfoot
- Spiderfoot has been removed. As an OSINT / reconnaissance tool it does not fit the defensive scope of T-Pot. The service, the `/spiderfoot/` route and the link on the landing page are gone, the Dockerfile has moved to `docker/deprecated/spiderfoot`.
- `update.sh` removes the `spiderfoot` service from a `docker-compose.yml` of your own, the previous file stays in the backup.
- Your scans in `~/tpotce/data/spiderfoot` are kept. If you no longer need them, remove the folder with `sudo rm -rf ~/tpotce/data/spiderfoot`.

### Galah
- In Elasticsearch the request and response fields of Galah are now `http_request.*` and `http_response.*` (i.e. `http_request.method`, `http_request.requestURI`), as ConPot and Miniprint log `request` / `response` as text and the daily index could only hold one of them. Import the Kibana objects of this release to get the updated Galah dashboard, older events keep the previous field names.

### T-Pot Config File
- `TPOT_PERSISTENCE` accepts only `on` / `off`, `TPOT_BLACKHOLE` and `TPOT_ATTACKMAP_TEXT` only `ENABLED` / `DISABLED`. Other values such as `true` passed the check before but were not acted on; with `TPOT_PERSISTENCE=true` the honeypot logs were deleted on every start. `update.sh` rewrites these values, T-Pot does not start with any other value.

### Beelzebub
- The LLM settings in `.env` are now `BEELZEBUB_LLM_PROVIDER` (`ollama` or `openai`), `BEELZEBUB_LLM_MODEL` (the model name, i.e. `openchat` or `gpt-4o`), `BEELZEBUB_LLM_HOST` and `BEELZEBUB_LLM_API_KEY`. `update.sh` migrates the previous settings (`BEELZEBUB_LLM_MODEL: "ollama"` / `"gpt4-o"`, `BEELZEBUB_OLLAMA_MODEL`), a `docker-compose.yml` of your own needs the new `environment` block of the `beelzebub` service from `compose/llm.yml`.

## Thanks & Credits
A heartfelt thank you to the contributors who made this release possible:
* @plygrnd for adding support for Red Hat Enterprise Linux!
* @regulartim for reporting #1866!
* Kevin Setz for pointing out exposed ENVs!
* @trixam for the fix of #1807!

… and to the entire T-Pot community for opening issues, sharing ideas, and helping improve T-Pot!
