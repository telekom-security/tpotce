# Package names of the personas

apt, yum 3 and opkg of the personas install only names that the release has; a name outside its
list gets the error text of the real package manager. The lists hold names only (no versions, no
descriptions, no dependencies), plus the virtual names of apt and the plain names that yum packages
provide.

| File | Releases | Content |
| --- | --- | --- |
| `apt.txt.gz` | `b` Debian 12 (bookworm), `t` Debian 13 (trixie), `r` Raspberry Pi OS bookworm (arm64), `j` Ubuntu 22.04 (jammy), `n` Ubuntu 24.04 (noble) | `<name> <release letters>` |
| `apt-virtual.txt.gz` | the same letters | `<virtual name> <release letters> <provider,...>` |
| `yum.txt.gz` | CentOS 7.9.2009 (os, updates, extras) | `<name>`, or `<provided name> <package>` |
| `opkg.txt.gz` | `a` OpenWrt 21.02.7, `b` OpenWrt 22.03.7 (mipsel_24kc, ramips/mt7621) | `<name> <release letters> <feed>` |

A release covers the components and pockets that the persona's own sources name:

- bookworm: main of bookworm, -updates, -security.
- trixie: main of trixie, -updates, -backports, -security.
- Raspberry Pi OS: main, contrib, non-free, non-free-firmware of bookworm, -updates, -security, plus the Raspberry Pi archive.
- jammy: main, restricted, universe; main and restricted of -updates; main, restricted, universe of -security.
- noble: main, restricted, universe, multiverse of noble, -updates, -backports, -security.

Ubuntu main and restricted and the Raspberry Pi archive are taken whole. The other indexes keep
the names in the top 20,000 of Debian popcon (`by_inst`) and the names of `EXTRA` in the builder.
Names of a kernel ABI are left out. At start the persona adds the names of its own kernel and the
packages it models (`system.APT_INSTALLED`, `APT_AVAILABLE`, the release's packages). A real name
that ranks lower is not found. Virtual names of libraries and language modules are left out, and
yum takes the provider with the shortest name (unverified: yum's own choice also weighs arch and
repository). yum and opkg keep their whole indexes, but yum has no sonames.

## Sources (downloaded 2026-10-13)

The download folder of the builder holds these files under these names:

| File | URL |
| --- | --- |
| `deb-<suite>-<component>-<arch>.xz` | https://deb.debian.org/debian/dists/<suite>/<component>/binary-<arch>/Packages.xz (suites bookworm, bookworm-updates, trixie, trixie-updates, trixie-backports) |
| `deb-<suite>-security-<component>-<arch>.xz` | https://security.debian.org/debian-security/dists/<suite>-security/<component>/binary-<arch>/Packages.xz |
| `rpi-bookworm-main-arm64.gz` | https://archive.raspberrypi.com/debian/dists/bookworm/main/binary-arm64/Packages.gz |
| `ub-<suite>-<component>-amd64.xz` | https://archive.ubuntu.com/ubuntu/dists/<suite>/<component>/binary-amd64/Packages.xz (suites noble, noble-updates, noble-backports, jammy, jammy-updates) |
| `ub-<suite>-security-<component>-amd64.xz` | https://security.ubuntu.com/ubuntu/dists/<suite>-security/<component>/binary-amd64/Packages.xz |
| `popcon-by_inst.gz` | https://popcon.debian.org/by_inst.gz |
| `c7-<repo>-primary.xml.gz` | https://vault.centos.org/7.9.2009/<repo>/x86_64/repodata/ (the `primary.xml.gz` that `repomd.xml` names; repos os, updates, extras) |
| `ow-<version>-core.gz` | https://downloads.openwrt.org/releases/<version>/targets/ramips/mt7621/packages/Packages.gz |
| `ow-<version>-<feed>.gz` | https://downloads.openwrt.org/releases/<version>/packages/mipsel_24kc/<feed>/Packages.gz (base, luci, packages, routing, telephony) |

The sha256 of each input:

| File | sha256 |
| --- | --- |
| `c7-extras-primary.xml.gz` | `6f4160fb1c4f94da6bf6d4295b3ae98fb4abcf01645072917b6b84acf87af159` |
| `c7-os-primary.xml.gz` | `2b479c0f3efa73f75b7fb76c82687744275fff78e4a138b5b3efba95f91e099e` |
| `c7-updates-primary.xml.gz` | `04fc3905eafcdc8422d475812ed81841986f18e3150b571c6920ba94b9c1898e` |
| `deb-bookworm-contrib-arm64.xz` | `2c8d4d73c617dee3178b63085b037371183a44573c3c0dc929cdfd86e9555e95` |
| `deb-bookworm-main-amd64.xz` | `9e0b5aabb2465b3d2e7a7fe27f9913846277833f7a2826e7767acccff5b588c5` |
| `deb-bookworm-main-arm64.xz` | `2ddb1737692e8c45c53e8d57c0ce4cd21c78c5703b830c3226b1423566a06c00` |
| `deb-bookworm-non-free-arm64.xz` | `5f70e8e31d64f9241904e54a83dc639d92f4eea9ad1a57b26d40f3943f474929` |
| `deb-bookworm-non-free-firmware-arm64.xz` | `a1330073ccb0787ab9864190877bccec16642f93f0dfaf9bdbff597c6326a6e3` |
| `deb-bookworm-security-contrib-arm64.xz` | `e0d2c18d6618ed1d768f9bc0110729c1f92f1ae59371d76c55db8d68fc28869b` |
| `deb-bookworm-security-main-amd64.xz` | `816aa77b2d8d70c7f17494007692511bc6848598909cff4ee8fc642231c40007` |
| `deb-bookworm-security-main-arm64.xz` | `4fb4648b49bee1f132b2d2e21c0812b15a5add9ced6ddc0b4314d20f8e80f01a` |
| `deb-bookworm-security-non-free-arm64.xz` | `c1e66449d14669229c89ee2fe7665a2b9d9bb4abb00e8486f0be0cfda36e2dbe` |
| `deb-bookworm-security-non-free-firmware-arm64.xz` | `15818983d3701516780d9b08c2e5dcefbb75ae5ee66afefa57581c26e7ae9f32` |
| `deb-bookworm-updates-contrib-arm64.xz` | `0040f94d11d0039505328a90b2ff48968db873e9e7967307631bf40ef5679275` |
| `deb-bookworm-updates-main-amd64.xz` | `87e7e94047fb7fb6f4ceecc7022d4bee55b66031cc2a7666d3196f3e0aabb846` |
| `deb-bookworm-updates-main-arm64.xz` | `5dfd52317071685ad6e58c4ef0720128b7db68624079f83be02bdf55f07ac070` |
| `deb-bookworm-updates-non-free-arm64.xz` | `0040f94d11d0039505328a90b2ff48968db873e9e7967307631bf40ef5679275` |
| `deb-bookworm-updates-non-free-firmware-arm64.xz` | `0040f94d11d0039505328a90b2ff48968db873e9e7967307631bf40ef5679275` |
| `deb-trixie-backports-main-amd64.xz` | `0b78cc1000b3ecfd67f298fc989ed3c27b78c0972b2c570715ba2e4728efe56e` |
| `deb-trixie-main-amd64.xz` | `7778d3e3f303b7ddb8ce0fe7c8d57473a076c6bf2e8f241f75421d2396352498` |
| `deb-trixie-security-main-amd64.xz` | `cf9ed1763e683ee0d51b976b6632e3b4c7f6c7abc965a1965ea85df07fd25b86` |
| `deb-trixie-updates-main-amd64.xz` | `10012cb384dc2613586d7b3d391d92a5637c9faa36017239739f2a6109a0d45f` |
| `ow-21.02.7-base.gz` | `93ec547a86ba2c9662090dfcada30fa593abd2d8ba4f268ae5f892654e364aa3` |
| `ow-21.02.7-core.gz` | `be894c4f14d2cea2842b5515ce32119c8f02d838377c76c6b719157e01d2c2c6` |
| `ow-21.02.7-luci.gz` | `300e998b681278925b882e66ef5fef0b45ca28342a9d03da61da56e00c4da8d9` |
| `ow-21.02.7-packages.gz` | `eb2fd091487d057f7d2ad967220b3e7a39e972831c68b1ca26da88ea3244fe36` |
| `ow-21.02.7-routing.gz` | `57c80620caaadeb56c20eca155b7e67a9b0d2c73f3c11dd4f5d88e34de45a40a` |
| `ow-21.02.7-telephony.gz` | `436f9f6d627e095090e9ba3c43dab4a67e4c5ea4ffde8b257bfd288c9ef1a1a2` |
| `ow-22.03.7-base.gz` | `db38fc1e41c1217ed9c15d286b315ff51d581627ba934b5be8065923c3d0728f` |
| `ow-22.03.7-core.gz` | `fdad002bb506b3b07c167e77974ae4a480bc72c3ebf6dd8aaa6883ebbdeb2d27` |
| `ow-22.03.7-luci.gz` | `65fed9b36a742c4253fa5566210c0a4a159d1f397c4a38e25b400d4b9dfe0b73` |
| `ow-22.03.7-packages.gz` | `7a5fdba3adef4f65b515915265210218e09f2cbd23cd2427e7cbd087e039610b` |
| `ow-22.03.7-routing.gz` | `c0631947ffb4ee09843e3e05c48c5a60f094e2f693ed8290c3c8ef49fe0c2ae1` |
| `ow-22.03.7-telephony.gz` | `b3538407ab2ce8d6ebb5a3c0b4a00107bb55e404f66e73d9ee32895a2ad4d965` |
| `popcon-by_inst.gz` | `a58f9cd45b6fe8f1f45ee16a289cc56d8e453a833753556d2a78d49cc971f44e` |
| `rpi-bookworm-main-arm64.gz` | `fdfa2c9010646fe76ec6d011efe28fd2dce7df7d3c8b381d66c28d6ad75c5bd2` |
| `ub-jammy-main-amd64.xz` | `37cb57f1554cbfa71c5a29ee9ffee18a9a8c1782bb0568e0874b7ff4ce8f9c11` |
| `ub-jammy-restricted-amd64.xz` | `92102b5d9dfb7804293891528d5e57c3d05949df71af613d4f99fcb7d6a3f488` |
| `ub-jammy-security-main-amd64.xz` | `cf1f011040c59e9123b1d5243caca7d223186cab6892d1101341a369030f7748` |
| `ub-jammy-security-restricted-amd64.xz` | `24c128c83879684b1776877d25d01e4924556a500fb1a904280a01c80c3ace61` |
| `ub-jammy-security-universe-amd64.xz` | `4f1ed259f79558c910de1a6facc9eaecd98030d6b9db51e8746a092e560bd7d7` |
| `ub-jammy-universe-amd64.xz` | `d29cb24c93fec5f43255706bce7eb46d4779952039d8c68ac1bb14a6f3655ce2` |
| `ub-jammy-updates-main-amd64.xz` | `6b6d61be8e24ba9b728ff4608df6912a02231afde62189dfe71ab300e7f1623f` |
| `ub-jammy-updates-restricted-amd64.xz` | `e81c86b18aa382b23fcdadb2bb2a874942f44b61d7d66ee885a3fcf9343d344c` |
| `ub-noble-backports-main-amd64.xz` | `b962c8d83acab25c27b3367cf94b8566d0da28bdf696eb324c263836d9e41a94` |
| `ub-noble-backports-multiverse-amd64.xz` | `e224a0da236577f82600151bef134d7ddb3eebd0fed8f4574f65dc7e88e0bf2d` |
| `ub-noble-backports-restricted-amd64.xz` | `0040f94d11d0039505328a90b2ff48968db873e9e7967307631bf40ef5679275` |
| `ub-noble-backports-universe-amd64.xz` | `4d689e32b0131a398ecbe07597d51b55074a20f70d66587c2c9c14d8f6fcc040` |
| `ub-noble-main-amd64.xz` | `2a6a199e1031a5c279cb346646d594993f35b1c03dd4a82aaa0323980dd92451` |
| `ub-noble-multiverse-amd64.xz` | `8080460fe0864ab72bfc2a6d52e76d8c82d55f2cc2188a12f83d6fbe3e1750ea` |
| `ub-noble-restricted-amd64.xz` | `aa3f9520f4e46cdcd610c7f0ce9450cda430f20085b028845e95122e9d61680c` |
| `ub-noble-security-main-amd64.xz` | `5c9b60d7fc31c9becdbaaa6443e3953b94ada356aedd84ce9113390a78cb4353` |
| `ub-noble-security-multiverse-amd64.xz` | `80ea3c5875729fc29893b7105f3e81a584fa4489f4fa701056ec1603f46b254c` |
| `ub-noble-security-restricted-amd64.xz` | `fa9d41fd43c8c7d784089207a1ca211d7d8dccefe1c6bb3c35ea382417f04673` |
| `ub-noble-security-universe-amd64.xz` | `d7e845a6768173e69332c106fed950dd846385474c394629c26ab93f1a83b102` |
| `ub-noble-universe-amd64.xz` | `ba9057fa1b91438cc8a1d26808d00c85389fe101d0c1496254df97236405599a` |
| `ub-noble-updates-main-amd64.xz` | `1b82f23ac8ff47ec232b711545febb7a596e0dce82e71cef7c65dd76023aa217` |
| `ub-noble-updates-multiverse-amd64.xz` | `f99e154c0eee0b2fa6a01c2a0ba7a19f68949c3296032bbec5cbdbaa110e42cf` |
| `ub-noble-updates-restricted-amd64.xz` | `4cb3139cdb7d3d8ef9ad806d0dcf5f663736accaf15a34e4120f2ba53ea7a634` |
| `ub-noble-updates-universe-amd64.xz` | `7648697581e4da732a404961c3d8477e36bf31d18d4cfbde68b080345eab9057` |

Rebuild the lists with `python3 -I docker/cowrie/tools/build_package_names.py DOWNLOADS`. The
builder only reads the names and the Provides; it runs nothing from the indexes.
