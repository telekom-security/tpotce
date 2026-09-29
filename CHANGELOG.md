<!-- Draft for T-Pot 24.04.2 - review and complete before the release -->
# Release Notes / Changelog
T-Pot 24.04.2 moves the Elastic Stack to 9.5 on the official Elastic images, makes updating a lot safer with backups you can actually restore, and adds new honeypots and NSM tooling.

## New Features
* **RDPHoneypot** a Remote Desktop honeypot for RDP connection and credential telemetry.
* **Satori** has been added as a passive fingerprinting NSM service running in parallel to P0f with normalized JSON logging.
* **Restore Script** `restore.sh` puts a backup written by `update.sh` back, as a whole or per group (checkout, configuration, `data/`, Kibana objects and ILM policy).
* **Update Script** has been reworked:
  * Backups go to `~/tpot_backups`, rotate, are checked for space and hold what git cannot bring back; `--full` adds all of `data/`.
  * Your Kibana objects and the ILM policy are exported before T-Pot is stopped.
  * The installed edition is detected and restored, `-b <branch>` / `-r <repo>` allow testing branches and forks, `-s` starts T-Pot after the update.
  * Before pulling a new Elastic Stack the update checks the disk space and warns if the backup does not hold the Elasticsearch data.
* **Cowrie Personas** let Cowrie present itself as different systems.
* **Smoke Tests** for the honeypot images (`docker/_tests`) and an end-to-end test of the Attack Map pipeline (`attackmap_pipeline_test.sh`).
* **Listbot** translation maps are cached.

## Updates
* **Elastic Stack** has been updated to 9.5.4 (from 8.16.1) and is now built on the official Elastic images.
* **Kibana** now runs with a 1 GB Node.js heap (`KIBANA_HEAP_MB` in `docker/elk/kibana/Dockerfile`) within a `mem_limit` of 2 GB.
* **Attack Map** has been updated to 4.0.0.
* **Beelzebub** has been updated to 3.9.2 and is built from upstream again instead of the T-Pot fork. The log format, the dashboards and the SSH host key stay as they were, further services (Telnet, MCP, LDAP, SMB, MSSQL, MQTT, RDP, PostgreSQL, VNC, Redis, Memcached) are prepared and can be enabled in the compose file.
* **Cowrie** has been updated to 3.0.0, pinned to a later commit of the main branch.
* **Suricata** has been updated to 8.0.7 (Alpine 3.24 package).
* **Nginx** has been updated to 1.28.3 (Alpine 3.23 package).
* **Cyberchef** has been updated to 11.0.0.
* **Elasticvue** has been updated to 1.15.0.
* **EWSPoster** has been updated to 1.33.
* **Go-Pot** has been updated to 1.2.0-rc-7.
* **H0neytr4p** has been updated to 0.44.
* **Hellpot** has been updated to 0.60.
* **IPPHoney** has been updated to 2.0.2.
* **Honeypots** without releases were updated to their latest pushed code, pinned to a commit.
* Docker images now use **Alpine 3.23** (Suricata 3.24), **Go 1.26** or **Scratch** wherever possible; **Log4Pot** moved from Ubuntu to Alpine.
* **Installer** supports unattended installations and has been tested with **Alma 10**, **Debian 13**, **Fedora 44**, **OpenSuse Tumbleweed**, **Rocky 10**, **RHEL 10** and **Ubuntu 26.04** (sudo-rs).
* **Persistence** cycles for logrotate are configurable through `TPOT_PERSISTENCE_CYCLES` in `.env`.
* **Beelzebub** and **Honeypots** log their status as text (i.e. `Stateless`, `failed`), it is now indexed as `status_text`. So far Elasticsearch could not index it in the numeric `status` field, some values ended up as `0` and with 24.04.1 part of these events were not indexed at all.
* Updates for `24.04.2` images will be provided continuously through Docker image updates.

## Breaking Changes
### Elastic Stack
- Elasticsearch and Kibana upgrade their data in `~/tpotce/data/elk` on the first start, ***this cannot be undone***. Run the update with `update.sh -y --full` or take a snapshot of the machine first, see [Elastic Stack Upgrades](README.md#elastic-stack-upgrades).
- If you use a `docker-compose.yml` of your own, raise the `mem_limit` of the `kibana` service to `2g`.
- The official Elastic images are based on UBI 9 and require a **x86-64-v2** capable CPU on x86 hosts, otherwise Elasticsearch, Kibana and Logstash stop with `Fatal glibc error: CPU does not support x86-64-v2`. Virtual machines using the legacy `kvm64` / `qemu64` CPU models are affected, e.g. Proxmox VE VMs created before 8.0 or QEMU without `-cpu`. Use the `host` CPU type or at least `x86-64-v2`.

### Update Script
- Updates are supported from **24.04.1** onwards. Installations of 24.04.0 need a fresh install.

### Dionaea
- The SIP service has been removed.

### Beelzebub
- The LLM settings in `.env` are now `BEELZEBUB_LLM_PROVIDER` (`ollama` or `openai`), `BEELZEBUB_LLM_MODEL` (the model name, i.e. `openchat` or `gpt-4o`), `BEELZEBUB_LLM_HOST` and `BEELZEBUB_LLM_API_KEY`. `update.sh` migrates the previous settings (`BEELZEBUB_LLM_MODEL: "ollama"` / `"gpt4-o"`, `BEELZEBUB_OLLAMA_MODEL`), a `docker-compose.yml` of your own needs the new `environment` block of the `beelzebub` service from `compose/llm.yml`.

## Thanks & Credits
A heartfelt thank you to the contributors who made this release possible:
* @plygrnd for adding support for Red Hat Enterprise Linux!
* @regulartim for reporting #1866!
* Kevin Setz for pointing out exposed ENVs!
* @trixam for the fix of #1807!

… and to the entire T-Pot community for opening issues, sharing ideas, and helping improve T-Pot!
