# Heralding 2.0 integration

The image builds the current master of `t3chn0m4g3/heralding` on Python 3.14 with uv
(`uv.lock` owns the dependencies). It runs as uid/gid 2000 with cap_net_bind_service,
a read-only rootfs and a writable `/tmp/heralding` tmpfs. Logs remain in `/var/log/heralding`.
No tests are shipped in the image.

## Build and test

```sh
# In the T-Pot checkout
docker compose -f docker/heralding/docker-compose.yml build heralding
./docker/_tests/run.sh heralding
# all 27 capabilities, RDP TLS/NLA and SIP TCP/UDP with standard clients
docker build -f docker/heralding/validation/Dockerfile.clients -t heralding:validation-tools docker/heralding/validation
docker/_tests/tests/heralding.sh --extended
# old settings and the free-port policy of every compose file
python3 -m unittest discover compose/tests
```

The source is not pinned, so an upstream change reaches the next build. The log format
that Logstash and ewsposter read (`auth.csv`, `log_session.json`) is guarded by
`validation/test_compat.py`, which needs a Heralding checkout:

```sh
uv run --project ../heralding pytest -q docker/heralding/validation/test_compat.py --import-mode=importlib
```

Both fixtures start and stop in that suite: `tpot_heralding_legacy.yml` keeps the old
16-service baseline, `tpot_heralding_current.yml` mirrors `dist/heralding.yml` with 27 services.

## Configuration and free ports

Existing capability settings, banners and log paths are preserved. New capabilities are
configured internally; host mappings are added only when the same port/protocol is free
in that profile. Every existing competing honeypot retains its mapping
(`compose/tests/test_heralding.py` checks both).

| Profile | Additional Heralding host ports |
|---|---|
| root, standard, sensor, mobile | 389, 636, 990, 8883 TCP |
| mac_win | above plus 445 TCP |
| tarpit | above plus 445, 587, 1433, 1883, 6379 TCP; 5060 TCP+UDP |
| tpot_services catalogue | 636, 990, 8883 TCP (389 belongs to honeypots) |
| standalone docker/heralding compose | all new service ports, including 8080 |

SMB stays on Dionaea's 445 in standard/sensor/mobile. In tarpit, 8080 stays with go-pot.
The SOCKS5 smoke condition accepts the old and current authentication reply versions.
This image writes `auth.csv` (from `dist/heralding.yml`), upstream's default is `log_auth.csv`.

## RDP

T-Pot keeps `tls_max_version: TLSv1_2` in its own config, the one change to the old
settings. Windows App 11.4.1 (3092) on macOS was confirmed on 2026-10-03: its TLS-only
fallback logged a plaintext username/password over TLS 1.2. Error 0x204 still appears
because the honeypot refuses the logon and provides no desktop. NLA captures NTLM hashes,
not a plaintext password (verified with FreeRDP and pyspnego). The extended smoke test
checks TLS 1.2 for both TLS and NLA captures and the joined MCS channels.
