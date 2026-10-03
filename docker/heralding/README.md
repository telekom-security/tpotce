# Heralding 2.0 integration

Python 3.14 with a pinned Python/uv build and source commit
`f3e9474c20da4950859b806a594f756757d20f7a` from `t3chn0m4g3/heralding`.
The image runs as uid/gid 2000 with cap_net_bind_service, read-only rootfs and a writable
`/tmp/heralding` tmpfs. Logs remain in `/var/log/heralding`. No tests are shipped in the image.

## Local-only build

The source commit has not been pushed. Use T-Pot's integration helper, which archives
the exact pinned commit into a temporary named build context and bypasses the remote fetch:

```sh
# In the T-Pot checkout, with heralding as a sibling checkout.
docker/heralding/validation/build_tpot.sh ../heralding heralding:tpot-dev
docker build -f docker/heralding/validation/Dockerfile.clients -t heralding:validation-tools docker/heralding/validation
docker/_tests/tests/heralding.sh --image heralding:tpot-dev --extended
uv run --project ../heralding pytest -q docker/heralding/validation/test_compat.py --import-mode=importlib
uv run --project ../heralding python docker/heralding/validation/check_tpot.py .
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
- Basic 11-service smoke and extended probes for all 27 capabilities, actual auth/session events.
- LDAP, LDAPS, FTPS and MQTTS credential capture using ldap3, ftplib and paho-mqtt.
- Free-port policy for every Heralding profile; `docker compose config --quiet` for all eight
  changed compose files.
- Installed package in site-packages, no tests in the image, uid 2000.
- Both T-Pot fixtures start/stop in T-Pot's own compatibility suite.
- FreeRDP TLS and NLA both capture credentials/material; NLA returns explicit logon failure.
- The upstream-oriented image keeps `log_auth.csv`; this image uses `auth.csv` from `dist/heralding.yml`.

The user confirmed Windows App 11.4.1 (3092) credential capture through its TLS-only
fallback; error 0x204 remains after capture. Authentication is deliberately refused,
and no remote desktop is provided. NLA captures NTLM hashes, not a plaintext password.


## RDP Windows App compatibility follow-up

The source pin includes the generic PER length fix, distinct MCS user-channel allocation
and RDP TLS 1.2 default. T-Pot explicitly keeps `tls_max_version: TLSv1_2` in its own config.
Generic Heralding also defaults to this ceiling and permits an explicit TLS 1.3 override.
The extended smoke checks TLS 1.2 for both TLS/NLA captures and validates the TLS-only
user-channel ID and the complete set of joined channels. Both compatibility fixtures
assert the effective TLS ceiling on the started RDP handler.

The user confirmed credential capture with Windows App 11.4.1 (3092) on macOS on
2026-10-03: after an initial NLA connection ended before sending a CredSSP message,
its TLS-only fallback logged one plaintext username/password attempt over TLS 1.2.
With four static channels, the user channel was 1008 and the joined channels were
1003 through 1008. No client credentials are retained here. Error 0x204 still appeared
because the honeypot ends the connection without a desktop or successful login.
This confirms the app's TLS-only fallback; FreeRDP and pyspnego separately verify NLA.
It does not prove NLA credential capture by this Windows App version or isolate the
contribution of TLS and channel fixes, which were changed together.

Current validation: 245 generic tests on macOS and Linux/Alpine, nine T-Pot compatibility
tests, all 27 capabilities in the extended image smoke and all eight free-port profiles.
The baseline comparison permits only the added RDP TLS ceiling; existing banners,
ports and other settings remain unchanged. Generic image: `heralding:generic-dev`.
T-Pot image: `heralding:tpot-dev`. The user's standalone Compose currently points to
`heralding:generic-dev` and was preserved. The older `heralding:2.0-dev` alias was not
rebuilt in this follow-up. No running user stack was restarted or image published.
