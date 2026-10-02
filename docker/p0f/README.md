# p0f for T-Pot

[p0f](https://lcamtuf.coredump.cx/p0f3/) identifies the operating system and software on both ends of a TCP connection purely passively, often from a single SYN, without sending a packet. T-Pot runs it as an NSM service next to Suricata. Its JSON log feeds Logstash and the Kibana dashboards **P0f** and **>T-Pot**.

This directory holds the p0f 3.09b C code by Michal Zalewski with T-Pot changes:

- **JSON log** (`-j`, jansson) with the fields Logstash and Kibana expect.
- **Its own fingerprint database** `p0f.fp`: current Windows, Linux, Android, macOS / iOS and IPv6 stacks, and the scanners masscan, zmap and option-less SYNs.
- **OS family and confidence per signature** (`conf`). The values come from public, labelled datasets and are written as `os_family`, `os_confidence` and `os_samples`.
- **User-Agent signals** in `http request` events: the User-Agent names another OS than the TCP stack, does not fit the header order, or the request comes through a proxy.
- **Offline mode**: the image reads pcap files and prints NDJSON on stdout.
- **VLAN tags** (802.1Q, QinQ) are skipped per packet. The original dropped every tagged packet.
- **Own SYNs filtered**: in live mode SYNs the T-Pot host sends itself are left out.

## Running in T-Pot

The `p0f` service in the T-Pot compose files runs with `network_mode: host` and writes to `~/tpotce/data/p0f/log/p0f.json`.

- **Capture interface:** `dist/capture-if.sh` picks it. That is `TPOT_CAPTURE_INTERFACE` from `.env`, otherwise the interface of the route to the internet.
- **Entrypoint:** without arguments, `dist/run.sh` starts p0f like this:

  ```
  p0f -u p0f -j -o /var/log/p0f/p0f.json -i <interface> \
      'not (outbound and ((ip and tcp[tcpflags] & (tcp-syn|tcp-ack) == tcp-syn) or (ip6 and ip6[6] == 6 and ip6[53] & 0x12 == 0x02)))'
  ```

  The filter drops outgoing SYNs without ACK by the packet direction of the kernel, so new or changed addresses (DHCP, SLAAC) are covered. p0f only tracks connections it saw the SYN of, so connections the host opens itself are not fingerprinted at all: neither the SYN+ACK nor the HTTP response of those servers. The host's own SYN+ACKs stay in, p0f needs them to follow incoming connections.
- **Logstash** (`docker/elk/logstash/dist/logstash.conf`, `type: P0f`) parses the timestamp and renames `client_ip` / `client_port` / `server_ip` / `server_port` to `src_ip` / `src_port` / `dest_ip` / `dest_port`. Nothing else is changed: every field below comes from p0f itself.

## Offline mode: pcap to JSON

With arguments the entrypoint passes them to p0f and adds `-j -o -`, unless `-j` or `-o` are given.

```
docker run --rm --network none -v "$PWD:/pcap:ro" dtagdevsec/p0f:24.04.2 -r /pcap/capture.pcap > capture.json
docker run --rm --network none -v "$PWD:/pcap:ro" dtagdevsec/p0f:24.04.2 -r /pcap/capture.pcap 'src net 203.0.113.0/24'
docker run --rm --network none -v "$PWD:/pcap:ro" -v "$PWD/my.fp:/fp:ro" dtagdevsec/p0f:24.04.2 -r /pcap/capture.pcap -f /fp
```

- **Output:** stdout holds one JSON object per line. Banner, progress and `All done` are dropped, warnings and errors go to stderr. So `| jq` or `| grep` never sees anything else.
- **Time stamps** come from the pcap, not from the time of the run.
- **Permissions:** the container runs as uid 2000. pcaps only you can read (i.e. `dumpcap` files with mode 0600) need `--user "$(id -u):$(id -g)"`.
- **Link types:** Ethernet (with up to two VLAN tags), Linux cooked v1, raw IP, loopback, PPP, PFLOG and 802.11 are known. For other types, i.e. Linux cooked v2 from `tcpdump -i any`, p0f finds the IP header in the first packet itself.
- **Not available:** `-d` and the API socket (`-s`) need a live capture. The direction filter of live mode does not apply, libpcap knows no direction in pcap files.

## Log format

Every line is one observation, `mod` names its type. All of them carry:

| Field | Content |
|---|---|
| `timestamp` | `YYYY/MM/DD HH:MM:SS`, UTC in the container |
| `mod` | type of observation, see below |
| `client_ip`, `client_port`, `server_ip`, `server_port` | the connection (`src_*` / `dest_*` in Elasticsearch) |
| `subject` | `cli` or `srv`: which end the observation is about |

| `mod` | Further fields |
|---|---|
| `syn` (client SYN), `syn+ack` (server answer) | `os` (OS label) or `app` (tool such as `NMap SYN scan`), `dist` (hops, `<= N` for tools with random TTL), `params`, `raw_sig`. Client SYNs add `os_family`, `os_confidence`, `os_samples` |
| `mtu` | `link` (i.e. `Ethernet or modem`, `DSL`, `generic tunnel or VPN`), `raw_mtu` |
| `uptime` | `uptime` (from TCP timestamps, modulo the counter wrap), `raw_freq` |
| `http request`, `http response` | `app` or `os`, `lang`, `params`, `raw_sig`. Requests add the User-Agent fields below |
| `host change`, `ip sharing` | `reason`, `raw_hits` (see [NAT detection](#nat-detection)) |

- `os` / `app` is `???` if no signature matched.
- `params` lists how the signature matched (`none` for an exact, specific match): `generic`, `fuzzy`, `random_ttl`, `tos:0xNN`; for HTTP also `dishonest`.
- Values that are no valid UTF-8 are logged with their bytes from 0x80 up as `\xNN`.

### OS family and confidence (client SYNs)

| Field | Content |
|---|---|
| `os_family` | `Android`, `Apple` (iOS and macOS), `Linux`, `Windows`, `BSD`, `Other`; `Scanner` for tools (labels of class `!`) |
| `os_confidence` | share of that family among the dataset flows p0f gives the same label (0 to 1) |
| `os_samples` | number of those flows |

Both come from the `conf` field of the matched label, see [below](#os-family-and-confidence-conf). Fuzzy matches and labels without data get the family alone, tools get no confidence. The Kibana visualization **P0f OS Distribution** counts client SYNs with `os_confidence >= 0.9` plus scanners.

### User-Agent signals (`http request`)

| Field | Content |
|---|---|
| `user_agent` | the User-Agent header |
| `ua_os` | OS family the User-Agent names (table `ua_family` in `p0f.fp`) |
| `os_family`, `os_confidence` | family and confidence of **this connection's** SYN |
| `ua_os_mismatch` | `true` if `ua_os` differs from the family of the SYN; Linux and Android count as one, a scanner SYN always differs. Only set if both are known |
| `ua_dishonest` | `true` if the User-Agent does not fit the HTTP header order (p0f's `dishonest`). Only set if the request has a User-Agent and the matched HTTP signature expects some software |
| `http_proxy` | `true` with a `Via` or `X-Forwarded-For` header: the TCP stack may be the proxy's, so a mismatch is no strong signal |

The dashboard **P0f** shows these signals. It leaves out requests through a proxy and mismatches against TCP fingerprints the datasets measured below a confidence of 0.9.

Limits:
- Only plain-text HTTP is visible to p0f.
- Only the first request of a connection is seen.
- Connections after a tool SYN are dropped on purpose (see [Limitations](#limitations)), so scanners never get HTTP events.

## Fingerprint database (`p0f.fp`)

`p0f.fp` is a text file. Lines starting with `;` are comments, blanks and CR at the end of a line are ignored. It is split into sections `[module:direction]`:

| Section | Matches |
|---|---|
| `[tcp:request]` | client SYN |
| `[tcp:response]` | server SYN+ACK |
| `[http:request]`, `[http:response]` | HTTP GET / HEAD requests and their responses |
| `[mtu]` | link types from the MSS |

`classes = win,unix,other` at the top registers the OS classes labels may use.

### Labels

Signatures follow a label they belong to:

```
label = type:class:name:flavor
sys   = @unix,Windows             ; only for class '!'
conf  = Linux:0.974:10987673      ; T-Pot, OS labels in [tcp:request] only
sig   = ...
```

| Part | Meaning |
|---|---|
| `type` | `s` for specific signatures, `g` for generic, last-resort ones. Generic signatures are only used if nothing specific matches |
| `class` | OS class (`win`, `unix`, `other`) for OS signatures, `!` for applications and tools (NMap, masscan, browsers) |
| `name` | short name (`Linux`, `Windows`, `masscan`). Keep it consistent, `os` in the log is name plus flavor |
| `flavor` | version or detail (`4.19 or newer`, `SYN scan`), may be empty |
| `sys` | required after labels of class `!`: OS names or `@classes` the software runs on |

### TCP signatures

```
sig = ver:ittl:olen:mss:wsize,scale:olayout:quirks:pclass
```

| Field | Meaning |
|---|---|
| `ver` | `4`, `6` or `*` for both |
| `ittl` | initial TTL (64, 128, 255, ...). A `-` suffix marks tools with random TTL. p0f prints new signatures as `observed+distance` |
| `olen` | length of IPv4 options, `0` for IPv6 |
| `mss` | MSS option, `*` if it depends on the link (then the `[mtu]` table names the link) |
| `wsize` | window: fixed, `mss*N`, `mtu*N`, `%N` (multiple of N) or `*` |
| `scale` | window scale, fixed or `*` |
| `olayout` | order of the TCP options: `mss`, `nop`, `ws`, `sok`, `sack`, `ts`, `eol+N` (end of options plus N padding bytes), `?N` (unknown option N); empty without options |
| `quirks` | comma-separated, see below |
| `pclass` | payload: `0` none, `+` some, `*` any |

| Quirk | Meaning |
|---|---|
| `df` | don't fragment set (IPv4) |
| `id+` | DF set, but IP ID not zero (IPv4) |
| `id-` | DF not set, but IP ID zero (IPv4) |
| `ecn` | explicit congestion notification |
| `0+` | "must be zero" field not zero (IPv4) |
| `flow` | IPv6 flow label not zero |
| `seq-` | sequence number zero |
| `ack+` | ACK number not zero, ACK flag not set |
| `ack-` | ACK number zero, ACK flag set |
| `uptr+` | URG pointer not zero, URG flag not set |
| `urgf+` | URG flag set |
| `pushf+` | PUSH flag set |
| `ts1-` | own timestamp zero |
| `ts2+` | peer timestamp not zero on the first SYN |
| `opt+` | non-zero data after the end of options |
| `exws` | window scale above 14 |
| `bad` | malformed TCP options |

Matching:
- p0f tries exact specific, then exact generic, then fuzzy matches. Fuzzy allows `df` / `id+` to disappear, `id-` / `ecn` to appear and a TTL off by more than the distance.
- Tool signatures never match fuzzy.
- A `*` version ignores IPv4-only quirks for IPv6 packets but keeps `flow`, so every IPv6 variant of a stack that sets a flow label needs its own `6:...:flow:0` signature.
- p0f refuses a signature that an earlier one already covers. That is why the scanner block stays at the top of `[tcp:request]`.

New SYN signatures: send a connection past p0f and copy `raw_sig`. The live log or the offline mode on a pcap both work.

### HTTP signatures

```
ua_os     = Linux,Windows,iOS=[iPad],iOS=[iPhone],Mac OS X,FreeBSD,OpenBSD,NetBSD,Solaris=[SunOS]
ua_family = Android=[Android],Apple=[iPhone],...,Linux=[X11]      ; T-Pot
sig       = ver:horder:habsent:expsw
```

| Field | Meaning |
|---|---|
| `ver` | `0` for HTTP/1.0, `1` for HTTP/1.1, `*` for both |
| `horder` | headers in the order they must appear, other headers may come between. `Name=[substring]` also checks the value, `?Name` marks a header that may be missing |
| `habsent` | headers that must not appear |
| `expsw` | substring the User-Agent (requests) or Server header (responses) should contain; if not, the match is flagged `dishonest` |

- **`ua_os`** maps User-Agent substrings to p0f OS names for p0f's NAT detection.
- **`ua_family`** (T-Pot) maps them to the families of the User-Agent signals:
  - The format is `Family=[substring]`, comma-separated, and the first match wins.
  - Families are those of `conf`.
  - Empty tables, a trailing comma, duplicates and entries an earlier, shorter substring always hides stop p0f with an error.
  - Desktop BSD and Solaris browsers also send `X11`, so `Linux=[X11]` has to come last.

### MTU signatures

```
label = DSL
sig   = 1492
```

Used for SYNs whose signature has `mss` = `*` (logged as `mod: mtu`), not for tools.

### OS family and confidence (`conf`)

```
conf = <family>[:<share>:<samples>]
```

- **Placement:** an optional field after an OS label (not class `!`) in `[tcp:request]`, before its first `sig`. It applies to all signatures of the label.
- **Values:**
  - Families are `Android`, `Apple`, `Linux`, `Windows`, `BSD` and `Other`.
  - `share` is a number from 0 to 1 (digits and a dot only), `samples` a positive count.
  - `conf = Linux` without share sets the family alone, for labels without data.
- **Errors:** a malformed field, an unknown family or a second `conf` for the same label stop p0f.

The `conf` fields are generated, do not edit them by hand. After changing signatures, rebuild the image and regenerate:

```
cd docker/p0f && docker compose build
python3 tools/build_os_confidence.py --cesnet <dir with subnet1-4.csv, local.csv> \
                                     --muni <flows_ground_truth_merged_anonymized.csv>
```

How the generator works:
- It reads two public, labelled datasets (both CC BY 4.0):
  - CESNET 2024 ([doi:10.5281/zenodo.14703490](https://doi.org/10.5281/zenodo.14703490)): M. Hulák, V. Bartoš, T. Čejka, *Transferability of TCP/IP-based OS fingerprinting models*, IFIP Networking 2025.
  - MUNI 2021 ([doi:10.5281/zenodo.7635138](https://doi.org/10.5281/zenodo.7635138)): M. Laštovička et al., *Passive operating system fingerprinting revisited: Evaluation and current challenges*, Computer Networks 229, 2023.
- It builds one synthetic SYN per key: window (or `mss*N`), TTL bucket, SYN size and source port class.
- Each SYN gets the option layout and window scale of the key's majority family.
- p0f from the image reads these SYNs, and the generator counts the families per matched label.

The fields it writes:
- Labels with at least 20 flows and a share of 0.5 or more get `conf = F:share:n`.
- Labels without enough data, and BSD / Other (no class in the datasets), get the short form.
- Labels whose own family has less than half of their flows get none, i.e. `Linux (barebone)`, which is mostly Windows SYNs with an MSS option only.
- The generator replaces its previous output, so running it twice gives the same file.
- The datasets carry no TCP option order and no scanner or honeypot traffic. The confidence is a measure on client networks, not on attack traffic.

## NAT detection

p0f compares observations of the same IP over time. A first big change gives a `host change` event, a lasting pattern `ip sharing`. `reason` lists why:

| Reason | Meaning |
|---|---|
| `os_sig` | detected OS differs from the earlier one |
| `sig_diff` | no OS match, but the TCP characteristics changed a lot |
| `app_vs_os` | detected application does not run on the host's OS |
| `x_known` | signature went from known to unknown or back |
| `tstamp`, `ttl`, `port`, `mtu`, `fuzzy` | TCP timestamps jumped, TTL changed, source port went down, MTU changed, match precision changed |
| `via` | Via / X-Forwarded-For in HTTP |
| `us_vs_os` | an honest-looking User-Agent names another OS |
| `app_srv_lb` | server signature changes (load balancing) |
| `date` | server date changes inconsistently |

## Building and testing

```
cd docker/p0f && docker compose build              # one image
cd docker/_builder && docker compose build p0f     # with the T-Pot build manifest (multi-arch)
./docker/_tests/run.sh p0f                         # smoke test, needs the image
./docker/_tests/tests/p0f.sh --skip-scanners       # without network access (no apk for nmap / masscan)
```

`build.sh` compiles p0f inside the image (`COMPILER-WARNINGS` there lists the warnings). The binary gets `cap_sys_chroot,cap_setgid,cap_net_raw` and runs as user `p0f` (uid 2000).

The smoke test (`docker/_tests/tests/p0f.sh`) checks:
- the Linux SYN label and its confidence;
- nmap and masscan as scanners;
- that no own SYNs show up, including from an address added later;
- the User-Agent signals;
- the parser errors for `conf` / `ua_family`;
- the offline mode (time stamps, VLAN / QinQ, BPF, own `-j` / `-o`, nothing but JSON on success).

## Limitations

- **Evasion:** fingerprints can be forged, and p0f can be evaded (i.e. by heavy fragmentation or bad checksums). Treat the output as a hint.
- **Tool SYNs end the connection:** after a SYN that matches a tool signature, p0f stops following the connection. That keeps scanner floods out of the connection table (default 1000 connections, 10000 hosts, `-m`), but there is no SYN+ACK, MTU, uptime or HTTP for those connections.
- **IPv6:** p0f does not parse extension headers. Packets with them are ignored.
- **Uptime:** needs about 25 ms of traffic with TCP timestamps from the same host. Clocks above 1.5 kHz are rejected.
- **SYN+ACK:** responses can depend on the options of the SYN, so one server can need several SYN+ACK signatures. `tools/p0f-sendsyn` helps collecting them.
- **Not used by T-Pot:** the API socket (`-s`, `tools/p0f-client`) and daemon mode (`-d`).

## Files

| Path | Content |
|---|---|
| `p0f.c`, `process.c`, `fp_*.c`, `readfp.c`, ... | p0f source with the T-Pot changes (marked `T-Pot:` in the comments) |
| `p0f.fp` | fingerprint database |
| `dist/run.sh` | entrypoint: live capture or offline mode |
| `dist/capture-if.sh` | capture interface, identical copies in `docker/{suricata,glutton}/dist/` and `docker/tpotinit/dist/bin/` |
| `tools/build_os_confidence.py` | generator for the `conf` fields |
| `tools/p0f-sendsyn*.c`, `tools/p0f-client.c` | upstream helpers: SYN+ACK collection, API client (see `tools/README-TOOLS`) |
| `docs/` | upstream ChangeLog, COPYING (LGPL 2.1), notes and extra signatures |

## License and credits

p0f is © 2012 Michal Zalewski and distributed under the GNU LGPL 2.1 (`docs/COPYING`). The T-Pot changes are under the same license. The `conf` values are derived from the CESNET 2024 and MUNI 2021 datasets (CC BY 4.0), please cite the papers above when you publish results based on them.

Upstream p0f thanks Phil Ames, Jannich Brendle, Matthew Dempsky, Jason DePriest, Dalibor Dukic, Mark Martinec, Damien Miller, Josh Newton, Nibbler, Bernhard Rabe, Chris John Riley, Sebastian Roschke, Peter Valchev, Jeff Weisberg, Anthony Howe, Tomoyuki Murakami and Michael Petch for their contributions.
