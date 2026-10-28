"""One package state per session: what the package managers of a persona know (system.py) and how
apt, dpkg, apt-cache, yum, rpm, dnf and opkg of one session agree on it (the patched commands)."""
import fnmatch
import gzip
import json
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests import test_patch
from tests.helpers import tmpdir
from tests.test_render import cfg_of, render_one
from tpot_personas import catalog, system
from tpot_personas.identity import draw


class RedHatTreeTest(unittest.TestCase):
    """What the Red Hat systems have of Debian's programs: neither the programs nor their manual pages; CentOS 7
    has no rbash (centos:7), its openssh-clients has slogin; RHEL 9 has neither (almalinux:9.4)."""

    def test_programs_and_manual_pages(self):
        from tests.test_render import fs_of
        for pid, slogin in (("centos-7", True), ("rhel-9", False)):
            _, out, _ = render_one(self, catalog.by_id(pid), 1)
            fs = fs_of(out)
            self.assertIsNone(test_patch.tree.find_node(fs, "usr/bin/rbash"), pid)
            self.assertEqual(any(fnmatch.fnmatchcase("usr/bin/slogin", p) for p in system.foreign_paths(
                catalog.by_id(pid))), not slogin, pid)
            gone = system.foreign_paths(catalog.by_id(pid))
            for rel in ("usr/share/man/man1/dash.1.gz", "usr/share/man/man1/mawk.1.gz", "usr/share/man/man8/run-parts.8.gz",
                        "usr/share/man/de/man1/sensible-editor.1.gz", "usr/share/man/man5/ucf.conf.5.gz",
                        "usr/share/man/man1/pdb3.11.1.gz"):
                self.assertTrue(any(fnmatch.fnmatchcase(rel, pattern) for pattern in gone), (pid, rel))
            self.assertFalse(any(fnmatch.fnmatchcase("usr/share/man/man1/tac.1.gz", pattern) for pattern in gone))
            self.assertEqual(any(fnmatch.fnmatchcase("usr/share/man/man1/slogin.1.gz", pattern) for pattern in gone),
                             not slogin, pid)

# tree of noble-updates (the ref of ubuntu:24.04): 2.1.1-2ubuntu3 and the stable release update of 24.04
NOBLE_TREE = "2.1.1-2ubuntu3.24.04.2"


class PackageDataTest(unittest.TestCase):
    """The data of the release a persona's apt starts its session with."""

    def test_meta_of_the_release(self):
        # the candidates of the releases' indexes (the refs of debian:12, debian:13, ubuntu:22.04, ubuntu:24.04)
        for pid, version, component, pocket, size in (
                ("debian-bookworm", "2.1.0-1", "main", "-", 52464), ("debian-trixie", "2.2.1-1", "main", "-", 59392),
                ("ubuntu-jammy", "2.0.2-1", "universe", "-", 47942),
                ("ubuntu-noble", NOBLE_TREE, "universe", "updates", 47360)):
            persona = catalog.by_id(pid)
            meta = system.apt_packages(persona, draw(persona, 1))["meta"]
            self.assertEqual(meta["tree"][:3], [version, component, pocket], pid)
            self.assertEqual(meta["tree"][4], size, pid)
            self.assertEqual(meta["tree"][-1], "displays an indented directory tree, in color", pid)
            self.assertEqual(system.apt_packages(persona, draw(persona, 1))["available"].get("tree"), None, pid)

    def test_service_packages_have_a_candidate_on_each_release(self):
        # the common service packages (apt show, apt-cache policy) have an apt-meta row on every apt release
        names = ("nginx apache2 mariadb-server postgresql redis-server docker.io curl wget net-tools python3-pip "
                 "git unzip screen tmux postfix").split()
        for pid in ("debian-bookworm", "debian-trixie", "ubuntu-jammy", "ubuntu-noble", "raspios"):
            persona = catalog.by_id(pid)
            ident = draw(persona, 1)
            letter = system._apt_letter(persona, ident)
            # apt search reads the names of apt.txt.gz; apt show reads the rows of apt-meta.txt.gz
            listed = {row[0] for row in system._name_rows("apt.txt.gz") if letter in row[1]}
            meta = system.apt_meta(persona, ident)
            for name in names:
                with self.subTest(persona=pid, name=name):
                    self.assertIn(name, listed)
                    self.assertIn(name, meta)

    def test_available_git_is_the_candidate_of_apt_meta(self):
        # apt install (available) and apt policy (meta) name one version; unzip, net-tools and screen still differ
        for pid in ("debian-bookworm", "debian-trixie", "raspios"):
            persona = catalog.by_id(pid)
            data = system.apt_packages(persona, draw(persona, 1))
            with self.subTest(persona=pid):
                self.assertEqual(data["available"].get("git", data["meta"]["git"][0]), data["meta"]["git"][0])

    def test_every_installed_package_has_a_description(self):
        for persona in catalog.PERSONAS:
            if persona.kind != "server" or not system.has_dpkg(persona):
                continue
            for seed in (1, 2):
                data = system.apt_packages(persona, draw(persona, seed))
                missing = [name for name in data["installed"] if not data["meta"].get(name, [""])[-1]]
                self.assertEqual(missing, [], (persona.id, seed))

    def test_kernel_packages_are_described_as_the_release_does(self):
        persona = catalog.by_id("ubuntu-jammy")
        ident = draw(persona, 1)
        release = ident.release.kernel_version
        meta = system.apt_packages(persona, ident)["meta"]
        self.assertEqual(meta[f"linux-image-{release}"][-1], "Signed kernel image generic")
        self.assertEqual(meta[f"linux-modules-{release}"][-1],
                         "Linux kernel extra modules for version 5.15.0 on 64 bit x86 SMP")
        self.assertEqual(meta[f"linux-headers-{release.rsplit('-', 1)[0]}"][-1],
                         "Header files related to Linux kernel version 5.15.0")
        persona = catalog.by_id("debian-bookworm")
        ident = draw(persona, 1)
        release = ident.release.kernel_version
        meta = system.apt_packages(persona, ident)["meta"]
        self.assertEqual(meta[f"linux-image-{release}"][-1], "Linux 6.1 for 64-bit PCs (signed)")
        self.assertEqual(meta[f"linux-headers-{release}"][-1], f"Header files for Linux {release}")

    def test_archives_and_files_of_the_release(self):
        for pid, archive in (("debian-bookworm", "oldstable"), ("debian-trixie", "stable"), ("ubuntu-noble", "noble"),
                             ("ubuntu-jammy", "jammy")):
            persona = catalog.by_id(pid)
            data = system.apt_packages(persona, draw(persona, 1))
            self.assertEqual(data["archives"]["-"], archive, pid)
            self.assertTrue(data["archives"]["updates"].startswith(archive + "-"), pid)
            self.assertGreater(data["files"], 20000, pid)
            self.assertEqual(data["files"], system.apt_packages(persona, draw(persona, 1))["files"], pid)

    def test_the_lists_of_the_persona_reach_the_session(self):
        """What the persona has installed (also packages other parts of the persona add to its lists)
        is the start of the session's package state."""
        persona = catalog.by_id("debian-bookworm")
        ident = draw(persona, 1)
        data = system.apt_packages(persona, ident)
        for name, version in ident.release.packages.items():
            self.assertEqual(data["installed"][name], version)


THIRTY = ("tree bc dc ed hello figlet cowsay nano lsof strace ltrace htop ncdu pv zip unzip whois telnet ftp socat "
          "netcat-openbsd rsync moreutils jq sl toilet bsdmainutils bsdextrautils mtr-tiny tcpdump").split()


class SessionCase(unittest.TestCase):
    """A session on the patched commands of a rendered persona (test_patch.PatchTest.upstream_session), on
    one patched Cowrie tree for the class."""

    root = None

    @classmethod
    def tearDownClass(cls):
        if cls.root is not None:
            shutil.rmtree(cls.root.parent, ignore_errors=True)
            cls.root = None

    def patched(self):
        if type(self).root is None:
            base = Path(tempfile.mkdtemp())
            root = base / "cowrie"
            shutil.copytree(test_patch.FIXTURE, root)
            test_patch.subprocess.run([test_patch.sys.executable, str(test_patch.PATCH), "--cowrie-root", str(root)],
                                      check=True, capture_output=True, text=True)
            type(self).root = root
        return type(self).root

    upstream_session = test_patch.PatchTest.upstream_session

    def session(self, pid, uid=0, extra_modules=()):
        run = self.upstream_session(pid, uid=uid, extra_modules=extra_modules)
        fs = run.protocol.fs
        removed, made = [], set()
        exists, mkfile = fs.exists, fs.mkfile

        def remove(path):
            removed.append(path)
            made.discard(path)

        def make(path, *args):
            made.add(path)
            return mkfile(path, *args)

        def usr(path):
            # /bin, /sbin and /lib are links to /usr in the trees of the personas
            return re.sub(r"^/(bin|sbin|lib)/", r"/usr/\1/", path)

        fs.remove, fs.mkfile = remove, make
        fs.exists = lambda path: usr(path) in {usr(p) for p in made} or (
            usr(path) not in {usr(p) for p in removed} and exists(path))
        run.removed = removed
        return run


class AptSessionTest(SessionCase):
    """apt, apt-get, dpkg, dpkg-query and apt-cache of one session agree (the refs of debian:12, debian:13,
    ubuntu:24.04: amd64 runs)."""

    def test_an_install_shows_in_every_tool(self):
        run = self.session("debian-bookworm")
        held = len(run.commands["dpkg"].call.__globals__["tpot_apt_upgradable"](run.protocol))
        out, err, code = run("apt-get install -y tree")
        self.assertEqual((err, code), ("", 0))
        self.assertIn("The following NEW packages will be installed:\n  tree\n"
                      f"0 upgraded, 1 newly installed, 0 to remove and {held} not upgraded.\n"
                      "Need to get 52.5 kB of archives.\n"
                      "After this operation, 116 kB of additional disk space will be used.\n"
                      "Get:1 http://deb.debian.org/debian bookworm/main amd64 tree amd64 2.1.0-1 [52.5 kB]\n", out)
        self.assertIn("Selecting previously unselected package tree.\r\n(Reading database ... \r(Reading database "
                      "... 5%\r", out)
        self.assertIn("Preparing to unpack .../tree_2.1.0-1_amd64.deb ...\r\nUnpacking tree (2.1.0-1) ...\r\n"
                      "Setting up tree (2.1.0-1) ...\r\n", out)
        self.assertEqual(run("dpkg -l tree")[0], (
            "Desired=Unknown/Install/Remove/Purge/Hold\n"
            "| Status=Not/Inst/Conf-files/Unpacked/halF-conf/Half-inst/trig-aWait/Trig-pend\n"
            "|/ Err?=(none)/Reinst-required (Status,Err: uppercase=bad)\n"
            "||/ Name           Version      Architecture Description\n"
            "+++-==============-============-============-=============================================\n"
            "ii  tree           2.1.0-1      amd64        displays an indented directory tree, in color\n"))
        self.assertEqual(run(["dpkg", "-l", "tre*"])[0], run("dpkg -l tree")[0])
        self.assertEqual(run("dpkg-query -W tree"), ("tree\t2.1.0-1\n", "", 0))
        self.assertEqual(run("apt-cache policy tree")[0], (
            "tree:\n  Installed: 2.1.0-1\n  Candidate: 2.1.0-1\n  Version table:\n *** 2.1.0-1 500\n"
            "        500 http://deb.debian.org/debian bookworm/main amd64 Packages\n"
            "        100 /var/lib/dpkg/status\n"))
        self.assertIn("\ntree/oldstable,now 2.1.0-1 amd64 [installed]\n", run("apt list --installed")[0])
        status = run("dpkg -s tree")[0]
        for line in ("Package: tree", "Status: install ok installed", "Section: utils", "Installed-Size: 113",
                     "Maintainer: Florian Ernst <florian@debian.org>", "Architecture: amd64", "Version: 2.1.0-1",
                     "Description: displays an indented directory tree, in color"):
            self.assertIn(line + "\n", status)
        self.assertIn("/usr/bin/tree\n", run("dpkg -L tree")[0])
        self.assertEqual(run("dpkg -S /usr/bin/tree")[0], "tree: /usr/bin/tree\n")
        out, err, code = run("apt-get install -y tree")
        self.assertIn("tree is already the newest version (2.1.0-1).\n", out)

    def test_remove_and_purge(self):
        run = self.session("debian-bookworm")
        run("apt-get install -y tree")
        files = run.protocol.tpot_apt_db["files"]
        out, err, code = run("apt-get remove -y tree")
        self.assertEqual((err, code), ("", 0))
        self.assertIn("The following packages will be REMOVED:\n  tree\n0 upgraded, 0 newly installed, 1 to remove", out)
        self.assertIn("After this operation, 116 kB disk space will be freed.\n", out)
        self.assertIn(f"(Reading database ... {files} files and directories currently installed.)\r\n"
                      "Removing tree (2.1.0-1) ...\r\n", out)
        self.assertIn("/usr/bin/tree", run.removed)
        self.assertEqual(run("dpkg -l tree"), ("", "dpkg-query: no packages found matching tree\n", 1))
        self.assertIn("Package 'tree' is not installed, so not removed\n", run("apt-get purge -y tree")[0])
        self.assertEqual(run("dpkg-query -W tree")[2], 1)
        # a package with conffiles stays as rc until it is purged
        run("apt-get install -y openbsd-inetd")
        run("apt-get remove -y openbsd-inetd")
        listing = run("dpkg -l openbsd-inetd")[0]
        self.assertRegex(listing, r"\nrc  openbsd-inetd +0\.20221205-2~deb12u1 +amd64 +OpenBSD Internet Superserver\n")
        self.assertIn("Status: deinstall ok config-files\n", run("dpkg -s openbsd-inetd")[0])
        out = run("apt-get purge -y openbsd-inetd")[0]
        self.assertIn("The following packages will be REMOVED:\n  openbsd-inetd*\n", out)
        self.assertIn("Purging configuration files for openbsd-inetd (0.20221205-2~deb12u1) ...\r\n", out)
        self.assertNotIn("After this operation", out)
        self.assertEqual(run("dpkg -l openbsd-inetd")[2], 1)

    def test_a_package_of_the_persona_is_in_the_state_and_can_go(self):
        run = self.session("debian-bookworm")
        version = run.ident.release.packages["curl"]
        self.assertRegex(run("dpkg -l curl")[0], rf"\nii  curl +{re.escape(version)} +amd64 +command line tool for "
                                                 "transferring data with URL syntax\n")
        self.assertEqual(run("dpkg-query -W curl")[0], f"curl\t{version}\n")
        out, err, code = run("apt-get remove -y curl")
        self.assertEqual(code, 0, err)
        self.assertIn("/usr/bin/curl", run.removed)
        self.assertNotIn("curl", run.protocol.tpot_apt_db["packages"])
        self.assertIn("E: Essential packages were removed", run("apt-get remove -y bash")[1])
        self.assertIn("bash", run.protocol.tpot_apt_db["packages"])

    def test_versions_and_releases(self):
        run = self.session("debian-bookworm")
        out, err, code = run("apt-get install -y tree=0.0.1")
        self.assertEqual((err, code), ("E: Version '0.0.1' for 'tree' was not found\n", 100))
        self.assertIn("Package tree is not available, but is referred to by another package.\n", out)
        out, err, code = run(["apt-get", "install", "-y", "tree/nosuchrelease"])
        self.assertEqual((err, code), ("E: Release 'nosuchrelease' for 'tree' was not found\n", 100))
        self.assertEqual(run("apt-get install -y tree=2.1.0-1")[2], 0)
        run("apt-get purge -y tree")
        self.assertEqual(run(["apt-get", "install", "-y", "tree/bookworm"])[2], 0)
        self.assertIn("tree", run.protocol.tpot_apt_db["packages"])

    def test_upgrade_takes_what_the_release_has_newer(self):
        run = self.session("debian-bookworm")
        upgradable = run.commands["dpkg"].call.__globals__["tpot_apt_upgradable"](run.protocol)
        out, err, code = run("apt-get autoremove -y")
        self.assertTrue(out.endswith(f"0 upgraded, 0 newly installed, 0 to remove and {len(upgradable)} not "
                                     "upgraded.\n"), out)
        out, err, code = run("apt-get upgrade -y")
        self.assertEqual(code, 0, err)
        for name, (old, new) in upgradable.items():
            self.assertIn(f"Unpacking {name}", out)
            self.assertIn(f"({new}) over ({old}) ...", out)
        self.assertTrue(run("apt-get dist-upgrade -y")[0].endswith(
            "0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\n"))

    def test_only_root_changes_the_state(self):
        run = self.session("debian-bookworm", uid=1000)
        for line in ("apt-get remove -y curl", "apt-get upgrade -y", "apt-get autoremove -y", "apt-get purge -y curl"):
            self.assertIn("Could not open lock file /var/lib/dpkg/lock-frontend", run(line)[1], line)
        self.assertEqual(run("dpkg -r curl"), ("", "dpkg: error: requested operation requires superuser privilege\n",
                                               2))
        self.assertEqual(run("dpkg -l curl")[2], 0)

    def test_dpkg_errors(self):
        run = self.session("debian-bookworm")
        self.assertEqual(run("dpkg -i /tmp/nosuch.deb")[1:],
                         ("dpkg: error: cannot access archive '/tmp/nosuch.deb': No such file or directory\n", 2))
        self.assertTrue(run("dpkg")[1].startswith("dpkg: error: need an action option\n"))
        self.assertEqual(run("dpkg -s nosuchpkg123"), (
            "", "dpkg-query: package 'nosuchpkg123' is not installed and no information is available\n"
                "Use dpkg --info (= dpkg-deb --info) to examine archive files.\n", 1))
        self.assertTrue(run("dpkg --version")[0].startswith(
            f"Debian 'dpkg' package management program version {run.ident.release.packages['dpkg']} (amd64).\n"))
        self.assertEqual(run("apt-cache policy nosuchpkg123")[1], "N: Unable to locate package nosuchpkg123\n")
        self.assertEqual(run("apt-cache show nosuchpkg123")[1:], ("N: Unable to locate package nosuchpkg123\n"
                                                                  "E: No packages found\n", 100))

    def test_apt_cache_show_has_the_fields_of_the_release(self):
        run = self.session("ubuntu-noble")
        out = run("apt-cache show tree")[0]
        self.assertTrue(out.startswith("Package: tree\nPriority: optional\nSection: universe/utils\nInstalled-Size: 108\n"
                                       "Maintainer: Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>\n"
                                       f"Architecture: amd64\nVersion: {NOBLE_TREE}\n"
                                       f"Filename: pool/universe/t/tree/tree_{NOBLE_TREE}_amd64.deb\n"
                                       "Size: 47360\n"), out)
        out, err, code = run("apt-get install -y tree")
        self.assertIn("Get:1 http://archive.ubuntu.com/ubuntu noble-updates/universe amd64 tree amd64 "
                      f"{NOBLE_TREE} [47.4 kB]\n", out)
        self.assertIn(f"\ntree/noble-updates,now {NOBLE_TREE} amd64 [installed]\n",
                      run("apt list --installed")[0])

    def test_apt_3_lists_in_columns_and_apt_get_wraps(self):
        # the lists of 30 packages on debian:13 (refs d13-09, d13-04, d13-11: no tty, 200 columns, apt-get)
        names = run_names = self.session("debian-trixie").commands["dpkg"].call.__globals__
        self.assertEqual(names["tpot_apt_columns"](THIRTY, 80), [
            "  bc             ed      jq         nano            sl       toilet",
            "  bsdextrautils  figlet  lsof       ncdu            socat    tree",
            "  bsdmainutils   ftp     ltrace     netcat-openbsd  strace   unzip",
            "  cowsay         hello   moreutils  pv              tcpdump  whois",
            "  dc             htop    mtr-tiny   rsync           telnet   zip"])
        self.assertEqual(run_names["tpot_apt_columns"](THIRTY, 200), [
            "  bc             bsdmainutils  dc  figlet  hello  jq    ltrace     mtr-tiny  ncdu            pv     sl     "
            "strace   telnet  tree   whois",
            "  bsdextrautils  cowsay        ed  ftp     htop   lsof  moreutils  nano      netcat-openbsd  rsync  socat  "
            "tcpdump  toilet  unzip  zip"])
        self.assertEqual(names["tpot_apt_wrap"](sorted(THIRTY), 80), (
            "  bc bsdextrautils bsdmainutils cowsay dc ed figlet ftp hello htop jq lsof\n"
            "  ltrace moreutils mtr-tiny nano ncdu netcat-openbsd pv rsync sl socat strace\n"
            "  tcpdump telnet toilet tree unzip whois zip\n"))

    def test_apt_3_writes_its_layout(self):
        run = self.session("debian-trixie")
        out, err, code = run(["apt", "install", "-y", "tree", "bc", "jq"])
        self.assertIn("Installing:\n  bc  jq  tree\n\nSummary:\n  Upgrading: 0, Installing: 3, Removing: 0, ", out)
        out, err, code = run(["apt", "remove", "-y", "tree", "bc", "jq"])
        self.assertIn("REMOVING:\n  bc  jq  tree\n\nSummary:\n  Upgrading: 0, Installing: 0, Removing: 3, ", out)
        self.assertEqual(code, 0, err)

class YumSessionTest(SessionCase):
    """yum 3 and rpm of CentOS 7 on one package state (the refs of centos:7, arm64 runs: aarch64 there)."""

    def test_the_table_and_steps_of_yum_3(self):
        names = self.session("centos-7").commands["rpm"].call.__globals__
        tree = {"arch": "aarch64", "evr": "0:1.6.0-10.el7", "repo": "base", "size": 46080, "isize": 84992}
        self.assertEqual(names["tpot_yum_table"]([("Installing", [("tree", tree, "base")])], "size"), [
            "=" * 80, " Package        Arch              Version                  Repository      Size", "=" * 80,
            "Installing:", " tree           aarch64           1.6.0-10.el7             base            45 k"])
        self.assertEqual(names["tpot_yum_table"]([("Removing", [("tree", tree, "@base")])], "isize")[1:5:3], [
            " Package        Arch              Version                 Repository       Size",
            " tree           aarch64           1.6.0-10.el7            @base            83 k"])
        def row(name, evr, repo, size):
            return name, {"arch": "aarch64", "evr": evr, "repo": repo, "size": size, "isize": 0}, repo

        self.assertEqual(names["tpot_yum_table"]([
            ("Installing", [row("postfix", "2:2.10.1-9.el7", "base", 2_621_440)]),
            ("Installing for dependencies", [row("mariadb-libs", "1:5.5.68-1.el7", "base", 759_808),
                                             row("systemd-sysv", "0:219-78.el7_9.9", "updates", 99_328)]),
            ("Updating for dependencies", [row("systemd", "0:219-78.el7_9.9", "updates", 5_138_022),
                                           row("systemd-libs", "0:219-78.el7_9.9", "updates", 405_504)])],
            "size")[1:], [
            " Package             Arch           Version                Repository      Size", "=" * 80,
            "Installing:", " postfix             aarch64        2:2.10.1-9.el7         base           2.5 M",
            "Installing for dependencies:", " mariadb-libs        aarch64        1:5.5.68-1.el7         base           742 k",
            " systemd-sysv        aarch64        219-78.el7_9.9         updates         97 k",
            "Updating for dependencies:", " systemd             aarch64        219-78.el7_9.9         updates        4.9 M",
            " systemd-libs        aarch64        219-78.el7_9.9         updates        396 k"])
        self.assertEqual(names["tpot_yum_step"]("Installing", "tree-1.6.0-10.el7.aarch64", 1, 1),
                         "  Installing : tree-1.6.0-10.el7.aarch64                                    1/1 \n")
        self.assertEqual(names["tpot_yum_step"]("Verifying", "1:mariadb-libs-5.5.68-1.el7.aarch64", 5, 7),
                         "  Verifying  : 1:mariadb-libs-5.5.68-1.el7.aarch64                          5/7 \n")

    def test_an_install_shows_in_rpm_and_yum(self):
        run = self.session("centos-7")
        out, err, code = run("yum install -y tree")
        self.assertEqual((err, code), ("", 0))
        for part in ("Resolving Dependencies\n--> Running transaction check\n"
                     "---> Package tree.x86_64 0:1.6.0-10.el7 will be installed\n--> Finished Dependency Resolution\n\n"
                     "Dependencies Resolved\n\n" + "=" * 80 + "\n",
                     "\nTransaction Summary\n" + "=" * 80 + "\nInstall  1 Package\n\nTotal download size: 46 k\n"
                     "Installed size: 87 k\nDownloading packages:\nRunning transaction check\nRunning transaction test\n"
                     "Transaction test succeeded\nRunning transaction\n",
                     "  Installing : tree-1.6.0-10.el7.x86_64 ", "\nInstalled:\n  tree.x86_64 0:1.6.0-10.el7 "):
            self.assertIn(part, out)
        self.assertTrue(out.endswith(" \n\nComplete!\n"), out[-60:])
        self.assertEqual(run("rpm -q tree"), ("tree-1.6.0-10.el7.x86_64\n", "", 0))
        self.assertIn("tree-1.6.0-10.el7.x86_64\n", run("rpm -qa")[0])
        self.assertEqual(run(["rpm", "-qa", "tre*"])[0], "tree-1.6.0-10.el7.x86_64\n")
        self.assertEqual(run("rpm -qf /usr/bin/tree")[0], "tree-1.6.0-10.el7.x86_64\n")
        self.assertIn("Package tree-1.6.0-10.el7.x86_64 already installed and latest version\nNothing to do\n",
                      run("yum install -y tree")[0])
        self.assertRegex(run("yum list installed tree")[0], r"Installed Packages\ntree\.x86_64 +1\.6\.0-10\.el7 +@base\n$")
        info = run("rpm -qi tree")[0]
        for line in ("Name        : tree", "Version     : 1.6.0", "Release     : 10.el7", "Architecture: x86_64",
                     "Summary     : File system tree viewer"):
            self.assertIn(line + "\n", info)
        out, err, code = run("yum remove -y tree")
        self.assertEqual(code, 0, err)
        self.assertIn("---> Package tree.x86_64 0:1.6.0-10.el7 will be erased\n", out)
        self.assertIn("Removing:\n tree ", out)
        self.assertIn("  Erasing    : tree-1.6.0-10.el7.x86_64 ", out)
        self.assertIn("\nRemoved:\n  tree.x86_64 0:1.6.0-10.el7 ", out)
        self.assertEqual(run("rpm -q tree"), ("package tree is not installed\n", "", 1))
        self.assertEqual(run("yum remove -y tree")[:2], ("Loaded plugins: fastestmirror\nNo Packages marked for removal\n",
                                                         "No Match for argument: tree\n"))

    def test_globs_and_what_packages_provide(self):
        run = self.session("centos-7")
        self.assertIn("---> Package tree.x86_64 0:1.6.0-10.el7 will be installed\n", run(["yum", "install", "-y", "tre*"])[0])
        # the provider of smtpdaemon is postfix, which the image has installed
        self.assertIn("Package 2:postfix-2.10.1-9.el7.x86_64 already installed and latest version\n",
                      run("yum install -y smtpdaemon")[0])
        run("yum remove -y postfix")
        self.assertIn("---> Package postfix.x86_64 2:2.10.1-9.el7 will be installed\n", run("yum install -y smtpdaemon")[0])
        self.assertIn("---> Package mailx.x86_64 0:12.5-19.el7 will be installed\n", run("yum install -y /bin/mail")[0])
        out, err, code = run("yum install -y /usr/bin/mail")
        self.assertEqual((err, code), ("Error: Nothing to do\n", 1))
        self.assertIn("No package /usr/bin/mail available.\n", out)
        self.assertEqual(run("rpm -q postfix mailx sendmail"), (
            "postfix-2.10.1-9.el7.x86_64\nmailx-12.5-19.el7.x86_64\npackage sendmail is not installed\n", "", 1))

    def test_search_list_and_info(self):
        run = self.session("centos-7")
        out = run("yum search tree")[0]
        self.assertIn("\n============================== N/S matched: tree ===============================\n", out)
        self.assertIn("\ntree.x86_64 : File system tree viewer\n", out)
        self.assertTrue(out.endswith("\n  Name and summary matches only, use \"search all\" for everything.\n"))
        self.assertRegex(run("yum list tree")[0], r"\nAvailable Packages\ntree\.x86_64 +1\.6\.0-10\.el7 +base\n$")
        info = run("yum info tree")[0]
        for line in ("Available Packages", "Name        : tree", "Arch        : x86_64", "Version     : 1.6.0",
                     "Release     : 10.el7", "Repo        : base/x86_64", "Summary     : File system tree viewer"):
            self.assertIn(line + "\n", info)
        self.assertEqual(run("yum list nosuchpkg123")[1:], ("Error: No matching Packages to list\n", 1))

    def test_rpm_knows_the_system_of_the_persona(self):
        run = self.session("centos-7")
        self.assertEqual(run("rpm --version")[0], f"RPM version {run.ident.release.packages['rpm'].split('-')[0]}\n")
        self.assertRegex(run("rpm -qf /bin/bash")[0], r"^bash-4\.2\.46-[0-9.a-z_]+\.x86_64\n$")
        # the test tree has no ssh-keygen: the owner of rpm -qf
        self.assertEqual(run.commands["rpm"].call.__globals__["tpot_rpm_owners"]()["/usr/bin/ssh-keygen"], "openssh")
        self.assertIn("openssh", run.protocol.tpot_yum_db if hasattr(run.protocol, "tpot_yum_db")
                      else run.commands["rpm"].call.__globals__["tpot_yum_db"](run.protocol))
        self.assertEqual(run("rpm -qf /bin/rbash")[1], "error: file /bin/rbash: No such file or directory\n")
        self.assertGreater(len(run("rpm -qa")[0].splitlines()), 30)


class DnfSessionTest(SessionCase):
    """dnf 4 and rpm of RHEL 9 (the refs of almalinux:9.4 with the 9.4 vault)."""

    def test_a_name_of_the_repositories_installs(self):
        run = self.session("rhel-9")
        out, err, code = run("dnf install -y tree")
        self.assertEqual((err, code), ("", 0))
        self.assertRegex(out, r"\nInstalling:\n tree +x86_64 +\S+\.el9 +rhel-9-for-x86_64-baseos(-eus)?-rpms +")
        self.assertRegex(run("rpm -q tree")[0], r"^tree-\S+\.el9\.x86_64\n$")
        self.assertEqual(run("dnf install -y nosuchpkg123")[1:], ("Error: Unable to find a match: nosuchpkg123\n", 1))
        self.assertIn("Installing:\n s-nail ", run("dnf install -y /usr/bin/mail")[0])
        self.assertIn("Installing:\n postfix ", run("dnf install -y smtpdaemon")[0])

    def test_the_build_outside_the_module_streams_installs(self):
        """RHEL 9.4 installs nginx 1.20.1, not the 1.24 of its nginx:1.24 stream; a name only a stream has is not
        found (refs t9.json el94-dnf-nginx, el94-dnf-modonly)."""
        run = self.session("rhel-9")
        out = run("dnf install -y nginx")[0]
        self.assertRegex(out, r"\n nginx +x86_64 +1:1\.20\.1-16\.el9_4\.1 +rhel-9-for-x86_64-appstream(-eus)?-rpms +")
        self.assertEqual(run("dnf install -y postgresql-server-devel")[1:],
                         ("Error: Unable to find a match: postgresql-server-devel\n", 1))

    def test_rpm_owners_of_rhel_9(self):
        run = self.session("rhel-9")
        self.assertEqual(run("rpm -qf /usr/bin/slogin"), ("", "error: file /usr/bin/slogin: No such file or directory\n",
                                                          1))
        self.assertEqual(run.commands["rpm"].call.__globals__["tpot_rpm_owners"]()["/usr/bin/ssh-keygen"], "openssh")
        self.assertRegex(run("rpm -qf /usr/bin/ssh")[0], r"^openssh-clients-8\.7p1-\S+\.x86_64\n$")
        self.assertIsNone(test_patch.tree.find_node(run.protocol.fs.fs, "usr/bin/slogin"))
        self.assertIsNone(test_patch.tree.find_node(run.protocol.fs.fs, "usr/bin/rbash"))
        info = run("rpm -qi bash")[0]
        self.assertTrue(info.startswith("Name        : bash\nVersion     : 5.1.8\nRelease     : 9.el9\n"
                                        "Architecture: x86_64\nInstall Date: "), info)
        self.assertEqual(run(["rpm", "-qa", "bas*"])[0], "basesystem-11-13.el9.noarch\nbash-5.1.8-9.el9.x86_64\n")

    def test_a_failed_transaction_leaves_no_files(self):
        run = self.session("rhel-9")
        fs, calls = run.protocol.fs, []
        make = fs.mkfile

        def full(path, *args):
            calls.append(path)
            return make(path, *args) if len(calls) < 3 else False

        fs.mkfile = full
        out, err, code = run("dnf install -y bind-utils")
        self.assertEqual(code, 1, (out, err))
        self.assertIn("needs", err)
        self.assertEqual(run.removed, calls[:2])
        self.assertEqual(run("rpm -q bind-utils")[2], 1)

    def test_dnf_marks_on_the_terminal_of_an_exec_with_a_pty(self):
        run = self.session("rhel-9")
        tty = run.commands["rpm"].call.__globals__["tpot_dnf_tty"]
        cmd = type("Cmd", (), {})()
        cmd.pp = type("Pp", (), {"targets": {1: ("terminal", None)}})()
        cmd.protocol = type("Exec", (), {"execcmd": "dnf install x"})()
        cmd.protocol.user = type("User", (), {})()
        self.assertFalse(tty(cmd))
        cmd.protocol.user.windowSize = (24, 80, 0, 0)
        self.assertTrue(tty(cmd))


class OpkgSessionTest(SessionCase):
    """opkg of OpenWrt: the versions and descriptions of the feeds (the refs of openwrt/rootfs 22.03.7)."""

    def test_list_shows_the_feeds_after_an_update(self):
        run = self.session("openwrt")
        version = "2.0.2-1"
        run("opkg update")
        self.assertEqual(run("opkg list tree")[0], f"tree - {version} - List contents of directories in a tree-like "
                                                   "format\n")
        self.assertIn("\n6in4 - ", run("opkg list")[0])
        out = run("opkg install tree")[0]
        self.assertIn(f"Installing tree ({version}) to root...\n", out)
        self.assertIn(f"/packages/tree_{version}_", out)
        self.assertIn(f"tree - {version}\n", run("opkg list-installed")[0])


class FixRoundTest(SessionCase):
    """The probes of the review of round 4 (track A), each a scenario of a session."""

    def test_apt_cache_show_sums_are_hashes_of_their_own(self):
        import hashlib
        run = self.session("debian-bookworm")
        fields = dict(line.split(": ", 1) for line in run("apt-cache show tree")[0].splitlines() if ": " in line)
        text = "tree 2.1.0-1 amd64".encode()
        self.assertEqual(fields["MD5sum"], hashlib.md5(text).hexdigest())
        self.assertEqual(fields["SHA256"], hashlib.sha256(text).hexdigest())
        self.assertFalse(fields["SHA256"].startswith(fields["MD5sum"]))

    def test_raspios_keeps_the_short_description(self):
        persona = catalog.by_id("raspios")
        meta = system.apt_packages(persona, draw(persona, 1))["meta"]
        self.assertEqual(meta["bash"][-1], "GNU Bourne Again SHell")
        self.assertTrue(all("\n" not in row[-1] and len(row[-1]) < 200 for row in meta.values()))

    def test_debian_has_its_base_system(self):
        for pid, present, absent in (("debian-bookworm", "/bin/ls", "/usr/bin/ls"),
                                     ("ubuntu-jammy", "/bin/ls", "/usr/bin/ls"),
                                     ("debian-trixie", "/usr/bin/ls", "/bin/ls"),
                                     ("ubuntu-noble", "/usr/bin/ls", "/bin/ls")):
            run = self.session(pid)
            self.assertEqual(run(f"dpkg -S {present}"), (f"coreutils: {present}\n", "", 0), pid)
            self.assertEqual(run(f"dpkg -S {absent}"), ("", f"dpkg-query: no path found matching pattern {absent}\n",
                                                         1), pid)
            status = run("dpkg -s grep")[0]
            self.assertTrue(status.startswith("Package: grep\nEssential: yes\nStatus: install ok installed\n"), status)
            self.assertRegex(run("dpkg -l libc6")[0], r"\nii  libc6:amd64 +\S+ +amd64 +GNU C Library: Shared libraries\n")
            self.assertIn("E: Essential packages were removed", run("apt-get remove -y grep")[1])

    def test_apt_get_simulates_without_changing_the_state(self):
        for uid in (0, 1000):
            run = self.session("debian-bookworm", uid=uid)
            out, err, code = run("apt-get -s install tree")
            self.assertEqual((err, code), ("", 0))
            self.assertRegex(out, r"\nInst tree \(2\.1\.0-1 Debian:12\.\d+/oldstable \[amd64\]\)\n"
                                  r"Conf tree \(2\.1\.0-1 Debian:12\.\d+/oldstable \[amd64\]\)\n$")
            self.assertEqual(out.startswith("NOTE: This is only a simulation!"), uid != 0)
            self.assertNotIn("tree", run.protocol.tpot_apt_db["packages"])
            for flag in ("--simulate", "--dry-run", "--just-print", "--no-act"):
                run(f"apt-get {flag} install tree")
                self.assertNotIn("tree", run.protocol.tpot_apt_db["packages"])
            out = run("apt-get -s upgrade")[0]
            self.assertIn("Calculating upgrade...\n", out)
            upgradable = run.commands["dpkg"].call.__globals__["tpot_apt_upgradable"](run.protocol)
            for name, (old, new) in upgradable.items():
                self.assertRegex(out, rf"Inst {re.escape(name)} \[{re.escape(old)}\] \({re.escape(new)} \S+ \[\w+\]\)\n")
            self.assertEqual(run.commands["dpkg"].call.__globals__["tpot_apt_upgradable"](run.protocol), upgradable)

    def test_reinstall_counts_as_reinstalled(self):
        run = self.session("debian-bookworm")
        out = run("apt-get install -y --reinstall curl")[0]
        self.assertIn(" 0 newly installed, 1 reinstalled, 0 to remove and ", out)
        self.assertNotIn("The following NEW packages", out)
        self.assertIn(") over (", out)

    def test_a_removed_package_of_the_persona_comes_back_as_cowries_command(self):
        run = self.session("ubuntu-noble", extra_modules=("git",))
        own = run.protocol.commands.get("git")
        self.assertIsNotNone(own)
        run("apt-get remove -y git")
        self.assertNotIn("git", run.protocol.commands)
        run("apt-get install -y git")
        self.assertIs(run.protocol.commands.get("git"), own)
        self.assertIs(run.protocol.commands.get("/usr/bin/git", own), own)

    def test_dpkg_r_counts_the_files(self):
        run = self.session("debian-bookworm")
        run("apt-get install -y tree")
        files = run.protocol.tpot_apt_db["files"]
        run("dpkg -r tree")
        self.assertLess(run.protocol.tpot_apt_db["files"], files)

    def test_yum_programs_and_their_removal(self):
        run = self.session("centos-7")
        made = []
        mkfile = run.protocol.fs.mkfile

        def make(path, *args):
            made.append(path)
            return mkfile(path, *args)

        run.protocol.fs.mkfile = make
        # (postfix of the image is installed: it goes first)
        run("yum remove -y postfix")
        run("yum install -y /bin/mail postfix")
        self.assertIn("/bin/mail", made)
        self.assertIn("/usr/sbin/postqueue", made)
        self.assertNotIn("/usr/bin/postfix", made)
        self.assertEqual(run("rpm -qf /bin/mail")[0], "mailx-12.5-19.el7.x86_64\n")
        run("yum remove -y mailx")
        self.assertIn("/bin/mail", run.removed)
        run("rpm -e wget")
        self.assertIn("/usr/bin/wget", run.removed)
        self.assertEqual(run("rpm -qf /usr/bin/wget")[1], "error: file /usr/bin/wget: No such file or directory\n")
        self.assertEqual(run("yum remove -y yum")[1:], ("Error: Trying to remove \"yum\", which is protected\n", 1))
        # the base system: yum and systemd need it (centos:7: yum remove glibc / bash)
        for name in ("glibc", "bash"):
            out, err, code = run(f"yum remove -y {name}")
            self.assertEqual((err, code), ('Error: Trying to remove "systemd", which is protected\n'
                                           'Error: Trying to remove "yum", which is protected\n', 1), name)
            self.assertIn(f"---> Package {name}.x86_64 ", out)
            self.assertTrue(out.endswith("--> Finished Dependency Resolution\n"), out[-80:])
        self.assertIn("bash", run.protocol.tpot_yum_db)

    def test_rpm_qi_dates_and_groups(self):
        import time
        run = self.session("centos-7")
        run("yum install -y tree")
        info = run("rpm -qi tree")[0]
        self.assertIn(f"Install Date: {time.strftime('%a %b %e', time.gmtime())} ", info)
        self.assertIn("Group       : Applications/File\n", info)
        self.assertIn("License     : GPLv2+\n", info)
        self.assertIn("URL         : http://mama.indstate.edu/users/ice/tree/\n", info)
        self.assertIn("Description :\nThe tree utility recursively displays the contents of directories in a\n", info)

    def test_yum_info_as_yum_3(self):
        run = self.session("centos-7")
        info = run("yum info tree")[0]
        self.assertIn("Available Packages\nName        : tree\nArch        : x86_64\nVersion     : 1.6.0\n"
                      "Release     : 10.el7\nSize        : 46 k\nRepo        : base/x86_64\n"
                      "Summary     : File system tree viewer\nURL         : http://mama.indstate.edu/users/ice/tree/\n"
                      "License     : GPLv2+\nDescription : The tree utility recursively displays the contents of "
                      "directories\n            : in a tree-like format.  Tree is basically a UNIX port of the DOS\n"
                      "            : tree utility.\n\n", info)
        run("yum install -y tree")
        info = run("yum info tree")[0]
        self.assertIn("Installed Packages\nName        : tree\n", info)
        self.assertIn("Size        : 87 k\nRepo        : installed\nFrom repo   : base\n", info)

    def test_yum_names_its_saved_transaction(self):
        run = self.session("centos-7")
        run("yum install tree")
        out = run.answer("n")[0]
        self.assertRegex(out, r"rerun it with:\n yum load-transaction /tmp/yum_save_tx\.\d{4}-\d\d-\d\d\.\d\d-\d\d\."
                              r"[a-z0-9_]{6}\.yumtx\n")

    def test_dnf_takes_the_builds_of_the_repositories(self):
        run = self.session("rhel-9")
        out = run("dnf list tree")[0]
        self.assertRegex(out, r"\nAvailable Packages\ntree\.x86_64 +1\.8\.0-10\.el9 +rhel-9-for-x86_64-baseos(-eus)?-rpms\n$")
        info = run("dnf info tree")[0]
        for line in ("Available Packages", "Name         : tree", "Version      : 1.8.0", "Release      : 10.el9",
                     "Architecture : x86_64", "Size         : 55 k", "Source       : tree-pkg-1.8.0-10.el9.src.rpm",
                     "Summary      : File system tree viewer", "URL          : http://mama.indstate.edu/users/ice/tree/",
                     "License      : GPLv2+ and LGPLv2+",
                     "Description  : The tree utility recursively displays the contents of directories in a"):
            self.assertIn(line + "\n", info)
        self.assertIn("\ntree.x86_64 : File system tree viewer\n", run("dnf search tree")[0])
        self.assertNotRegex(run("dnf search tree")[0], r"\.x86_64 : \n")
        out = run("dnf install -y tree")[0]
        self.assertRegex(out, r"\n tree +x86_64 +1\.8\.0-10\.el9 +rhel-9-for-x86_64-baseos(-eus)?-rpms +55 k\n")
        info = run("rpm -qi tree")[0]
        self.assertIn("Summary     : File system tree viewer\nDescription :\nThe tree utility recursively", info)


class FixRound2Test(SessionCase):
    """The probes of the re-review of round 4 (track A)."""

    def test_rhel_names_no_rebuild(self):
        for seed in (1, 2):
            _, out, _ = render_one(self, catalog.by_id("rhel-9"), seed)
            for path in sorted(out.rglob("*")):
                if path.is_file():
                    self.assertNotIn(b"alma", path.read_bytes().lower(), (seed, path.name))
        run = self.session("rhel-9")
        for line in ("dnf install -y nginx", "rpm -qi nginx", "rpm -qi systemd", "dnf info systemd", "dnf info nginx",
                     "dnf search kernel", "dnf list dnf", "rpm -qa"):
            out, err, _ = run(line)
            self.assertNotIn("alma", (out + err).lower(), line)

    def test_rpm_qi_takes_the_repositories_fields_of_its_build_only(self):
        run = self.session("rhel-9")
        version = run("rpm -q systemd")[0].strip()
        info = run("rpm -qi systemd")[0]
        release = re.search(r"^Release     : (\S+)$", info, re.M).group(1)
        source = re.search(r"^Source RPM  : (\S+)$", info, re.M).group(1)
        self.assertIn(release, source, info)
        self.assertTrue(version.startswith("systemd-"), version)

    def test_apt_options_with_an_s_install(self):
        run = self.session("debian-bookworm")
        out, err, code = run(["apt-get", "install", "-y", "-oAPT::Install-Recommends=0", "tree"])
        self.assertEqual(code, 0, err)
        self.assertNotIn("Inst tree", out)
        self.assertIn("tree", run.protocol.tpot_apt_db["packages"])
        run("apt-get purge -y tree")
        for args in (["-qs", "install", "tree"], ["-o", "Debug::NoLocking=1", "-s", "install", "tree"],
                     ["-c", "/etc/apt/apt.conf.d/x", "--simulate", "install", "tree"]):
            out, err, code = run(["apt-get", *args])
            self.assertIn("\nInst tree ", out, args)
            self.assertNotIn("tree", run.protocol.tpot_apt_db["packages"], args)
        out, err, code = run(["apt-get", "-o", "-s", "install", "-y", "tree"])
        self.assertIn("tree", run.protocol.tpot_apt_db["packages"])

    def test_dnf_without_patterns_answers_at_once(self):
        import time
        run = self.session("rhel-9")
        run("dnf search tree")
        for line in ("dnf search tree", "dnf list", "dnf info"):
            start = time.perf_counter()
            run(line)
            self.assertLess(time.perf_counter() - start, 0.1, line)

    def test_dnf_info_of_a_session_install(self):
        run = self.session("rhel-9")
        run("dnf install -y tree")
        info = run("dnf info tree")[0]
        self.assertRegex(info, r"Installed Packages\nName         : tree\n(.+\n)*Repository   : @System\n"
                               r"From repo    : rhel-9-for-x86_64-baseos(-eus)?-rpms\n")

    def test_rpm_names_the_owner_through_the_links_of_usr(self):
        run = self.session("centos-7")
        run("yum install -y /bin/mail")
        for path in ("/bin/mail", "/usr/bin/mail"):
            self.assertEqual(run(f"rpm -qf {path}")[0], "mailx-12.5-19.el7.x86_64\n", path)
        self.assertRegex(run("rpm -qf /bin/bash")[0], r"^bash-")
        self.assertRegex(run("rpm -qf /usr/bin/bash")[0], r"^bash-")


class FixRound3Test(SessionCase):
    """The probes of the evidence check of round 4 (track A)."""

    def test_regex_names_as_apt_matches_them(self):
        import time
        for prog in ("apt-get", "apt"):
            run = self.session("debian-bookworm")
            out, err, code = run([prog, "install", "-y", "^tre.$"])
            self.assertEqual(code, 0, err)
            self.assertIn("Setting up tree (2.1.0-1) ...", out)
        run = self.session("debian-bookworm")
        start = time.perf_counter()
        out, err, code = run(["apt-get", "install", "-y", "a*a*a*a*a*a*a*a*a*a*a*b$"])
        self.assertLess(time.perf_counter() - start, 1.0)
        self.assertEqual(code, 100)
        names = run.commands["dpkg"].call.__globals__["tpot_apt_regex"]("^lib")
        self.assertEqual(len(names), 64)
        self.assertTrue(all(name.startswith("lib") for name in names))

    def test_apt_show_and_invalid_operations(self):
        run = self.session("debian-bookworm")
        record = ("Package: tree\nVersion: 2.1.0-1\nPriority: optional\nSection: utils\n"
                  "Maintainer: Florian Ernst <florian@debian.org>\nInstalled-Size: 116 kB\nDownload-Size: 52.5 kB\n"
                  "{manual}APT-Sources: http://deb.debian.org/debian bookworm/main amd64 Packages\n"
                  "Description: displays an indented directory tree, in color\n\n")
        out, err, code = run("apt show tree")
        self.assertEqual((out, code), (record.format(manual=""), 0))
        self.assertIn("WARNING: apt does not have a stable CLI interface", err)
        run("apt-get install -y tree")
        self.assertEqual(run("apt show tree")[0], record.format(manual="APT-Manual-Installed: yes\n"))
        self.assertEqual(run("apt show nosuchpkg123")[1:], (
            "\nWARNING: apt does not have a stable CLI interface. Use with caution in scripts.\n\n"
            "E: No packages found\n", 100))
        self.assertEqual(run("apt nosuchcommand")[1:], (
            "\nWARNING: apt does not have a stable CLI interface. Use with caution in scripts.\n\n"
            "E: Invalid operation nosuchcommand\n", 100))
        for line in ("apt-get nosuchcommand", "apt-get show tree"):
            self.assertEqual(run(line), ("", f"E: Invalid operation {line.split()[1]}\n", 100), line)
        run = self.session("debian-trixie")
        self.assertEqual(run("apt nosuchcommand")[1].splitlines()[-1], "Error: Invalid operation nosuchcommand")
        self.assertEqual(run("apt-get install -y hello")[2], 0)

    def test_yum_names_a_command_it_does_not_have(self):
        run = self.session("centos-7")
        self.assertEqual(run("yum nosuchcommand"), ("Loaded plugins: fastestmirror\n",
                                                   "No such command: nosuchcommand. Please use /usr/bin/yum --help\n", 1))

    def test_rpm_ql_lists_the_files(self):
        for pid in ("centos-7", "rhel-9"):
            run = self.session(pid)
            self.assertEqual(run("rpm -ql nosuchpkg123"), ("package nosuchpkg123 is not installed\n", "", 1), pid)
            run(f"{'yum' if pid == 'centos-7' else 'dnf'} install -y tree")
            out, err, code = run("rpm -ql tree")
            self.assertEqual(code, 0, err)
            self.assertTrue(out.startswith("/usr/bin/tree\n"), out)
            self.assertIn("/usr/share/man/man1/tree.1.gz\n", out)
            self.assertNotIn("tree-1.", out.splitlines()[0])



class FixRound4Test(SessionCase):
    """The findings of the re-review of round 3 (track A)."""

    def test_regex_names_of_one_command_share_one_time_budget(self):
        import time
        words = [f".+.+.+3q|.3{chr(97 + n)}" for n in range(16)]
        for line in (["apt-get", "install", "-y"], ["apt", "install", "-y"], ["apt-get", "remove", "-y"]):
            run = self.session("debian-bookworm")
            start = time.perf_counter()
            out, err, code = run(line + words)
            self.assertLess(time.perf_counter() - start, 1.0, line)
            self.assertEqual(code, 100, (line, err))
        run = self.session("debian-bookworm")
        out, err, code = run(["apt-get", "install", "-y", "^tre.$"])
        self.assertIn("Setting up tree (2.1.0-1) ...", out)


    def test_apt_names_an_option_it_does_not_have(self):
        """debian:12, debian:13 (refs/packages-a4.json d12o-*, d13o-*): the error of apt's command line, apt in
        colour also on a pipe, nothing installed."""
        tail = " is not understood in combination with the other options"
        run = self.session("debian-bookworm")
        self.assertEqual(run("apt install --nosuchoption tree"), (
            "", f"\x1b[1;31mE: \x1b[0mCommand line option --nosuchoption{tail}\x1b[0m\n", 100))
        self.assertEqual(run("dpkg -l tree")[2], 1)
        for line, text in (("apt-get install --nosuchoption tree", f"Command line option --nosuchoption{tail}"),
                           ("apt-get install -Z tree", f"Command line option 'Z' [from -Z]{tail}."),
                           ("apt-get install --nosuchoption=1 tree", f"Command line option --nosuchoption=1{tail}"),
                           ("apt-get install --no-target-release tree",
                            "Command line option --no-target-release is not boolean"),
                           ("apt-get install tree --target-release", "Option --target-release requires an argument."),
                           ("apt-get install -s --foo-yes tree", "Sense foo is not understood, try true or false."),
                           ("apt-get remove --nosuchoption tree", f"Command line option --nosuchoption{tail}"),
                           ("apt-get install --update tree", f"Command line option --update{tail}")):
            self.assertEqual(run(line), ("", f"E: {text}\n", 100), line)
        self.assertEqual(run("apt-get install --no-install-recommends --YES -o Dpkg::Use-Pty=0 tree")[2], 0)
        run = self.session("debian-bookworm", uid=1000)
        self.assertEqual(run("apt install --nosuchoption tree")[1:], (
            f"\x1b[1;31mE: \x1b[0mCommand line option --nosuchoption{tail}\x1b[0m\n", 100))
        run = self.session("debian-trixie")
        self.assertEqual(run("apt install --nosuchoption tree"), (
            "", f"\x1b[1;31mError: \x1b[0m\x1b[1mCommand line option --nosuchoption{tail}\x1b[0m\n", 100))
        self.assertEqual(run("apt-get install --update -y tree")[2], 0)


    def test_yum_provides_as_yum_3_answers(self):
        """centos:7 (refs/packages-a4.json c7y-*, refs/packages.json c7-38, c7x-02..04): the packages of a name,
        a program or a provided name, available ones first, installed ones from their repository."""
        head = "Loaded plugins: fastestmirror\nLoading mirror speeds from cached hostfile\n"
        tree = "tree-1.6.0-10.el7.x86_64 : File system tree viewer\nRepo        : {repo}\n{matched}\n\n\n"
        run = self.session("centos-7")
        self.assertEqual(run("yum provides tree"), (head + tree.format(repo="base", matched=""), "", 0))
        self.assertEqual(run("yum whatprovides tree"), (head + tree.format(repo="base", matched=""), "", 0))
        self.assertEqual(run("yum provides /usr/bin/tree"), (head + tree.format(
            repo="base", matched="Matched from:\nFilename    : /usr/bin/tree\n"), "", 0))
        self.assertEqual(run("yum provides tree nosuchthing123zz"), (head + tree.format(repo="base", matched=""), "", 0))
        # postfix of the image is installed (its master runs): the base package, then the installed one
        postfix = ("2:postfix-2.10.1-9.el7.x86_64 : Postfix Mail Transport Agent\nRepo        : {repo}\n"
                   "Matched from:\nProvides    : smtpdaemon\n\n\n\n")
        sendmail = ("sendmail-8.14.7-6.el7.x86_64 : A widely used Mail Transport Agent (MTA)\nRepo        : base\n"
                    "Matched from:\nProvides    : smtpdaemon\n\n\n\n")
        self.assertEqual(run("yum provides smtpdaemon"), (
            head + postfix.format(repo="base") + sendmail + postfix.format(repo="@anaconda"), "", 0))
        self.assertEqual(run("yum provides /bin/mail")[0].splitlines()[2:6], [
            "mailx-12.5-19.el7.x86_64 : Enhanced implementation of the mailx command", "Repo        : base",
            "Matched from:", "Filename    : /bin/mail"])
        for word in ("/usr/bin/mail", "nosuchthing123zz"):
            self.assertEqual(run(f"yum provides {word}"), (head + "No matches found\n", "", 0), word)
        self.assertEqual(run("yum provides"), ("Loaded plugins: fastestmirror\n", (
            "Error: Need an item to match\n Mini usage:\n\nprovides SOME_STRING\n\nFind what package provides "
            "the given value\n\nalias: whatprovides\n"), 1))
        run("yum install -y tree")
        self.assertEqual(run("yum provides tree"), (head + tree.format(repo="base", matched="")
                                                    + tree.format(repo="@base", matched=""), "", 0))
        out = run("yum provides bash")[0]
        self.assertTrue(out.endswith("Repo        : @anaconda\n\n\n\n"), out)
        run = self.session("centos-7", uid=1000)
        self.assertEqual(run("yum provides tree")[0], head + tree.format(repo="base", matched=""))

    def test_yum_history_of_the_session(self):
        """centos:7 (c7y-10, c7y-15, c7y-18): one user's transactions name their command lines, those of two users
        their users; the installation is the first transaction."""
        rule = "-" * 79
        run = self.session("centos-7")
        count = len(run("rpm -qa")[0].splitlines())
        out, err, code = run("yum history")
        self.assertEqual((err, code), ("", 0))
        lines = out.splitlines()
        self.assertEqual(lines[:3], ["Loaded plugins: fastestmirror",
                                     "ID     | Command line             | Date and time    | Action(s)      | Altered",
                                     rule])
        self.assertRegex(lines[3], r"^     1 \|                          \| \d{4}-\d\d-\d\d \d\d:\d\d \| Install        \| "
                         + f"{count:4d}   $")
        self.assertEqual(lines[4:], ["history list"])
        self.assertEqual(run("yum history list"), (out, "", 0))
        run("yum install -y tree")
        run("yum remove -y tree")
        lines = run("yum history")[0].splitlines()
        self.assertEqual(lines[1], "ID     | Login user               | Date and time    | Action(s)      | Altered")
        self.assertRegex(lines[3], r"^     3 \| root <root>              \| [-0-9: ]{16} \| Erase          \|    1   $")
        self.assertRegex(lines[4], r"^     2 \| root <root>              \| [-0-9: ]{16} \| Install        \|    1   $")
        self.assertRegex(lines[5], r"^     1 \| System <unset>           \| [-0-9: ]{16} \| Install        \| ")
        self.assertEqual(lines[6:], ["history list"])
        run = self.session("centos-7", uid=1000)
        self.assertEqual(run("yum history"), ("Loaded plugins: fastestmirror\n",
                                              "You don't have access to the history DB.\n", 1))


    def test_apt_search_of_the_release(self):
        """debian:12, debian:13, ubuntu:24.04 (refs/packages-a4.json d12s-*, d13s-*, u2404s-*): every pattern is a
        regular expression of the name or the description in any case, the rows of apt list."""
        warning = "\nWARNING: apt does not have a stable CLI interface. Use with caution in scripts.\n\n"
        head = "Sorting...\nFull Text Search...\n"
        record = "tree/oldstable{now} 2.1.0-1 amd64{mark}\n  displays an indented directory tree, in color\n\n"
        for uid in (0, 1000):
            run = self.session("debian-bookworm", uid=uid)
            self.assertEqual(run(["apt", "search", "^tree$"]), (head + record.format(now="", mark=""), warning, 0))
        run = self.session("debian-bookworm")
        for words in (["^TREE$"], ["tree", "indented"], ["--names-only", "^tree$"]):
            self.assertEqual(run(["apt", "search", *words])[0], head + record.format(now="", mark=""), words)
        self.assertEqual(run("apt search nosuchpkg123zz"), (head, warning, 0))
        self.assertEqual(run("apt search"), ("", warning + "E: You must give at least one search pattern\n", 100))
        self.assertEqual(run(["apt", "search", "^tree$"], tty=True), (
            "Sorting... Done\nFull Text Search... Done\n\x1b[32mtree\x1b[0m/oldstable 2.1.0-1 amd64\n"
            "  displays an indented directory tree, in color\n\n", "", 0))
        self.assertEqual(run(["apt-cache", "search", "^tree$"]), (
            "tree - displays an indented directory tree, in color\n", "", 0))
        self.assertEqual(run("apt-cache search nosuchpkg123zz"), ("", "", 0))
        self.assertEqual(run("apt-cache search"), ("", "E: You must give at least one search pattern\n", 100))
        names = [line.split("/")[0] for line in run("apt search --names-only ^tre")[0].splitlines()[2::3]]
        self.assertEqual(names, sorted(names))
        self.assertIn("tree", names)
        run("apt-get install -y tree")
        self.assertEqual(run(["apt", "search", "^tree$"])[0], head + record.format(now=",now", mark=" [installed]"))
        run = self.session("debian-trixie")
        self.assertEqual(run(["apt", "search", "^tree$"], tty=True)[0], (
            "\x1b[32mtree\x1b[0m/stable 2.2.1-1 amd64\n  displays an indented directory tree, in color\n\n"))
        self.assertEqual(run("apt search")[1].splitlines()[-1], "Error: You must give at least one search pattern")
        run = self.session("ubuntu-noble")
        self.assertEqual(run(["apt", "search", "^tree$"])[0], head + (
            f"tree/noble-updates {NOBLE_TREE} amd64\n  displays an indented directory tree, in color\n\n"))


    def test_rpm_ql_names_only_the_manual_pages_of_the_data(self):
        """rpm -ql lists the manual pages the repositories' filelists give the package's programs and the
        session's tree holds (an install puts them there); none for glibc's helpers in /usr/sbin."""
        for pid, tool in (("centos-7", "yum"), ("rhel-9", "dnf")):
            run = self.session(pid)
            out = run("rpm -ql glibc")[0]
            self.assertNotIn("/usr/share/man/", out, pid)
            # centos:7 (refs/packages-a4.json c7q-01): the helpers of the system's own architecture only
            self.assertNotIn(".i686", out, pid)
            run(f"{tool} install -y tree")
            out = run("rpm -ql tree")[0].splitlines()
            self.assertEqual(out[0], "/usr/bin/tree", pid)
            self.assertEqual([line for line in out if "/man/" in line], ["/usr/share/man/man1/tree.1.gz"], pid)
            self.assertTrue(run.protocol.fs.exists("/usr/share/man/man1/tree.1.gz"), pid)
            for line in run("rpm -ql openssh-clients")[0].splitlines():
                if "/man/" in line:
                    self.assertTrue(run.protocol.fs.exists(line), (pid, line))
            run(f"{tool} remove -y tree")
            self.assertFalse(run.protocol.fs.exists("/usr/share/man/man1/tree.1.gz"), pid)



class FixRound5Test(SessionCase):
    """The findings of the re-review of round 4 (track A)."""

    def test_name_scans_of_one_session_share_one_time_budget(self):
        """The commands of one line run back to back: their name scans take of the session's time (TPOT_BUDGET)
        together; a scan past it finds nothing, as for a name the release does not have."""
        import time
        hard = [f"lib[^x]*[^x]*[^x]*n{chr(97 + n)}" for n in range(8)] + [f".*.*.*[0-9]{chr(97 + n)}$" for n in range(8)]
        run = self.session("debian-bookworm")
        start = time.perf_counter()
        for _ in range(20):
            self.assertEqual(run(["apt-get", "install", "-y", *hard])[2], 100)
        for _ in range(5):
            run(["apt", "search", *hard[8:]])
        self.assertLess(time.perf_counter() - start, 1.0)
        out, err, code = run(["apt-get", "install", "-y", "^tre.$"])
        self.assertEqual((code, err.splitlines()[0]), (100, "E: Unable to locate package ^tre.$"))
        # a new window of the session
        for slot in run.protocol.user.tpot_budget.slots:
            slot[0] -= len(run.protocol.user.tpot_budget.slots)
        self.assertIn("Setting up tree (2.1.0-1) ...", run(["apt-get", "install", "-y", "^tre.$"])[0])
        for pid, tool in (("centos-7", "yum"), ("rhel-9", "dnf")):
            run = self.session(pid)
            globs = [f"*{chr(97 + n)}*e*e*e*e*e*e*q*" for n in range(16)]
            start = time.perf_counter()
            for _ in range(20):
                run([tool, "install", "-y", *globs])
                run([tool, "list", *globs])
                if tool == "yum":
                    run([tool, "provides", *globs])
            self.assertLess(time.perf_counter() - start, 1.0, pid)


    def test_a_short_option_takes_its_value_after_an_equals_sign(self):
        """debian:12 (refs/packages-a4.json d12r-01, -02, -07): -q=2 and -o=X=1 are understood."""
        run = self.session("debian-bookworm")
        # (the quiet levels of -q are not modelled: the progress lines stay)
        self.assertEqual(run("apt-get -s -q=2 -o=Debug::X=1 install nosuchpkg123zz")[1:],
                         ("E: Unable to locate package nosuchpkg123zz\n", 100))
        out, err, code = run("apt-get -y -q=2 install tree")
        self.assertEqual(code, 0, err)
        self.assertIn("Setting up tree (2.1.0-1) ...", out)
        self.assertEqual(run("apt-get -y -Z=2 install tree")[1],
                         "E: Command line option 'Z' [from -Z=2] is not understood in combination with the other "
                         "options.\n")

    def test_install_without_packages_says_the_sessions_summary(self):
        """debian:12, debian:13 (d12r-04..06, d13r-04..06): the three Reading lines and the summary of the
        session's own upgradable packages."""
        for pid in ("debian-bookworm", "debian-trixie"):
            run = self.session(pid)
            held = len(run.commands["dpkg"].call.__globals__["tpot_apt_upgradable"](run.protocol))
            lines = ("Reading package lists...\nBuilding dependency tree...\nReading state information...\n"
                     f"0 upgraded, 0 newly installed, 0 to remove and {held} not upgraded.\n")
            for line in ("apt-get install", "apt-get -y -f install", "apt-get install -y"):
                self.assertEqual(run(line), (lines, "", 0), (pid, line))
            out, err, code = run("apt install")
            three = ("Reading package lists...\nBuilding dependency tree...\nReading state information...\n"
                     f"Summary:\n  Upgrading: 0, Installing: 0, Removing: 0, Not Upgrading: {held}\n")
            self.assertEqual((out, code), (three if pid == "debian-trixie" else lines, 0), pid)


    def test_apt_search_names_only_packages_with_a_description(self):
        """apt search and apt-cache search leave out the names the persona has no description of."""
        run = self.session("debian-bookworm")
        meta = run.commands["dpkg"].call.__globals__["tpot_apt_data"]()["meta"]
        out = run("apt search --names-only ^tre")[0].splitlines()[2:]
        rows = list(zip(out[0::3], out[1::3]))
        self.assertIn("tree", [row.split("/")[0] for row, _ in rows])
        for row, description in rows:
            name = row.split("/")[0]
            self.assertIn(name, meta, row)
            self.assertNotEqual(description.strip(), name, row)
        for line in run("apt-cache search ^tre")[0].splitlines():
            name, _, description = line.partition(" - ")
            self.assertIn(name, meta, line)
            self.assertNotEqual(description, name, line)
        self.assertNotIn("tre-agrep", run("apt search agrep")[0] if "tre-agrep" not in meta else "")
        for _ in range(30):
            self.assertIn("tree/oldstable", run("apt search ^tree$")[0])


    def test_yum_repolist_clean_and_makecache(self):
        """centos:7 against the vault (refs/packages-a4.json c7r-*, c7c-*): the persona's repositories (base,
        extras, updates of 7.9.2009, x86_64 package counts of the vault), the session's cache."""
        plugins = "Loaded plugins: fastestmirror\n"
        table = ("repo id                            repo name                              status\n"
                 "base/x86_64                        CentOS-7 - Base                        10,072\n"
                 "extras/x86_64                      CentOS-7 - Extras                         526\n"
                 "updates/x86_64                     CentOS-7 - Updates                      6,173\n"
                 "repolist: 16,771\n")
        cached, fresh = "Loading mirror speeds from cached hostfile\n", "Determining fastest mirrors\n"
        repos = "Cleaning repos: base extras updates\n"
        run = self.session("centos-7")
        self.assertEqual(run("yum repolist"), (plugins + cached + table, "", 0))
        self.assertEqual(run("yum repolist enabled"), (plugins + cached + table, "", 0))
        self.assertEqual(run("yum makecache fast"), (plugins + cached + "Metadata Cache Created\n", "", 0))
        self.assertEqual(run("yum makecache"), (plugins + cached + "Metadata Cache Created\n", "", 0))
        self.assertEqual(run("yum clean packages"), (plugins + repos + "0 package files removed\n", "", 0))
        self.assertEqual(run("yum clean dbcache"), (plugins + repos + "18 sqlite files removed\n", "", 0))
        self.assertEqual(run("yum clean metadata"), (plugins + repos + "7 metadata files removed\n"
                                                    "0 sqlite files removed\n0 metadata files removed\n", "", 0))
        self.assertEqual(run("yum clean metadata"), (plugins + repos + "0 metadata files removed\n"
                                                    "0 sqlite files removed\n0 metadata files removed\n", "", 0))
        self.assertEqual(run("yum clean all"), (plugins + repos + "Cleaning up list of fastest mirrors\n", "", 0))
        self.assertEqual(run("yum clean all"), (plugins + repos, "", 0))
        self.assertEqual(run("yum clean"), (plugins, "Error: clean requires an option: headers, packages, metadata, "
                                                     "dbcache, plugins, expire-cache, rpmdb, all\n", 1))
        self.assertEqual(run("yum clean nosuch"), (plugins, "Error: invalid clean argument: 'nosuch'\n Mini usage:\n\n"
                                                            "clean [headers|packages|metadata|dbcache|plugins|"
                                                            "expire-cache|all]\n\nRemove cached data\n", 1))
        self.assertEqual(run("yum repolist"), (plugins + fresh + table, "", 0))
        self.assertEqual(run("yum repolist")[0], plugins + cached + table)
        run("yum clean all")
        self.assertEqual(run("yum makecache fast"), (plugins + fresh + "Metadata Cache Created\n", "", 0))
        self.assertEqual(run("yum clean metadata")[0].splitlines()[2], "7 metadata files removed")
        # a command that reads the repositories fetches their metadata again
        run("yum repolist")
        self.assertEqual(run("yum clean dbcache")[0].splitlines()[2], "18 sqlite files removed")
        run = self.session("centos-7", uid=1000)
        self.assertEqual(run("yum repolist")[0], plugins + cached + table)


APT_QUIET = json.loads((Path(__file__).parent / "fixtures" / "apt_quiet.json").read_text(encoding="utf-8"))
APT_QUIET_MORE = json.loads((Path(__file__).parent / "fixtures" / "apt_quiet_more.json").read_text(encoding="utf-8"))


class AptRule:
    """The quiet rule of the patch (tpot_apt_quiet, tpot_apt_quieter), run on recorded texts: what apt leaves of the text
    of a run at level 1 (as without -q on a pipe); below level 2 nothing changes."""

    # the persona globals of the first session, kept on the class
    functions = None

    def rule(self, name):
        if AptRule.functions is None:
            AptRule.functions = self.session("debian-bookworm").commands["dpkg"].call.__globals__
        return AptRule.functions[name]

    def level(self, flags: str) -> int:
        return self.rule("tpot_apt_quiet")(SimpleNamespace(args=flags.split()))

    def quieter(self, prog: str, text: str, level: int, answer: bool = True) -> str:
        if level < 2:
            return text
        return self.rule("tpot_apt_quieter")(SimpleNamespace(tpot_prog=prog), text, level, answer)


class AptQuietTest(AptRule, SessionCase):
    """apt-get and apt with -q, -qq, -q=2, --quiet, --quiet=2, -qqq (fixtures/apt_quiet.json: debian:9, debian:10,
    debian:12, debian:13, ubuntu:22.04, ubuntu:24.04 in containers)."""

    CASE = re.compile(r"(apt-get|apt) ((?:-\S+ )*)(update|install|remove|purge)\b ?(.*)")

    def recorded(self):
        """{(image, terminal, prog, verb, rest, -y): {level: (stdout, status)}} of the recorded runs."""
        found = {}
        for image, data in APT_QUIET["images"].items():
            for terminal, table in ((False, data["cases"]), (True, data["cases_tty"])):
                for case, (out, _, status) in table.items():
                    prog, flags, verb, rest = self.CASE.fullmatch(case).groups()
                    yes = rest == "zzhello" and any(re.fullmatch(r"-[a-z]*y[a-z]*", word) for word in flags.split())
                    found.setdefault((image, terminal, prog, verb, rest, yes), {}).setdefault(
                        self.level(flags), []).append((out, status, case))
        return found

    def test_the_rule_is_what_the_recorded_apts_do(self):
        checked = 0
        for (image, terminal, prog, verb, rest, yes), levels in self.recorded().items():
            # on a terminal the run without -q draws percentages: the base is -q there
            base_level = 1 if terminal else 0
            base_out, base_status, base_case = levels[base_level][0]
            for level, results in levels.items():
                if terminal and level == 0:
                    continue
                for out, status, case in results:
                    with self.subTest(image=image, case=case, terminal=terminal):
                        self.assertEqual(out, self.quieter(prog, base_out, level), case)
                        self.assertEqual(status, base_status)
                        checked += 1
        self.assertEqual(checked, 1416)

    def test_the_fetch_lines_of_a_repository_that_copies(self):
        # compares the rule with the recording only, no persona runs here
        copy = APT_QUIET["copy_repo"]
        # stderr's warning, its blank lines and debconf's note come at other places in a merged text
        plain = lambda text: "".join(  # noqa: E731
            line for line in text.splitlines(keepends=True) if line.strip() and not line.startswith(("WARNING:", "debconf:")))
        for prog in ("apt-get", "apt"):
            base = copy[f"{prog} -y install zzhello"]
            for flags in ("-q", "-qq", "-qqq"):
                case = f"{prog} {flags} -y install zzhello"
                if case in copy:
                    with self.subTest(case=case):
                        self.assertEqual(plain(copy[case]), plain(self.quieter(prog, base, self.level(flags))))

    def commands(self, prog, flags):
        return [f"{prog} {flags} -y install curl", f"{prog} {flags} -y install nosuchpkg123",
                f"{prog} {flags} -y remove nosuchpkg123", f"{prog} {flags} -y remove tree",
                f"{prog} {flags} -y autoremove", f"{prog} {flags} update"]

    def test_the_persona_leaves_out_what_the_recorded_apts_leave_out(self):
        for pid in ("debian-bookworm", "debian-trixie", "ubuntu-jammy", "ubuntu-noble"):
            for prog in ("apt-get", "apt"):
                base = None
                for flags in ("", "-q", "-qq", "-q=2", "--quiet", "--quiet=2", "-qqq"):
                    run = self.session(pid)
                    got = [run(line) for line in self.commands(prog, flags)]
                    if flags == "":
                        base = got
                        continue
                    for line, (out, err, code), (base_out, base_err, base_code) in zip(self.commands(prog, flags),
                                                                                       got, base):
                        with self.subTest(persona=pid, line=line):
                            self.assertEqual(out, self.quieter(prog, base_out, self.level(flags)))
                            self.assertEqual((err, code), (base_err, base_code))

    def test_a_terminal_writes_the_plain_lines_with_a_quiet_option(self):
        plain = "Reading package lists...\nBuilding dependency tree...\nReading state information...\n"
        for pid in ("debian-bookworm", "debian-trixie", "ubuntu-jammy", "ubuntu-noble"):
            for prog in ("apt-get", "apt"):
                run = self.session(pid)
                with self.subTest(persona=pid, prog=prog):
                    one = run(f"{prog} -q -y install curl", tty=True)[0]
                    self.assertTrue(one.startswith(plain), one)
                    self.assertEqual(run(f"{prog} --quiet=2 -y install curl", tty=True)[0],
                                     self.quieter(prog, one, 2))
                    self.assertEqual(run(f"{prog} -qqq -y install curl", tty=True)[0], self.quieter(prog, one, 3))

    def test_an_install_and_a_remove_keep_the_dpkg_lines_and_drop_the_rest(self):
        for pid in ("debian-bookworm", "debian-trixie", "ubuntu-noble"):
            for prog in ("apt-get", "apt"):
                texts = {}
                for flags in ("", "-qq", "-qqq"):
                    run = self.session(pid)
                    texts[flags] = (run(f"{prog} {flags} -y install tree")[0], run(f"{prog} {flags} -y remove tree")[0])
                for flags, level in (("-qq", 2), ("-qqq", 3)):
                    for number in (0, 1):
                        with self.subTest(persona=pid, prog=prog, flags=flags, step=number):
                            self.assertEqual(texts[flags][number], self.quieter(prog, texts[""][number], level))
                self.assertIn("Setting up tree", texts["-qqq"][0])
                self.assertNotIn("Get:", texts["-qq"][0])
                self.assertIn("Removing tree", texts["-qqq"][1])
                self.assertNotIn("After this operation", texts["-qqq"][1])
                self.assertEqual(self.session(pid)(f"{prog} -qqy install curl")[0], "" if prog == "apt-get" else
                                 self.quieter(prog, self.session(pid)(f"{prog} -y install curl")[0], 2))


    IMAGE_PERSONAS = {"debian_12": "debian-bookworm", "debian_13": "debian-trixie", "ubuntu_22.04": "ubuntu-jammy",
                      "ubuntu_24.04": "ubuntu-noble"}
    MORE_CASE = re.compile(r"(?:(refused|unresolvable): )?(apt-get|apt|apt-cache)((?: (?:-q+|--quiet\S*|-q=\d))*) "
                           r"(.*?)( \(zzhello automatic\))?")

    def test_the_other_verbs_and_the_offline_texts_are_what_the_recorded_apts_do(self):
        # list, show, search, policy, upgrade, autoremove, autopurge, reinstall, check and update without a network
        # (fixtures/apt_quiet_more.json): the text at -q, -qq, -qqq against the rule applied to the text without -q
        checked = 0
        for image, data in APT_QUIET_MORE["images"].items():
            for table in ("cases", "offline"):
                groups = {}
                for case, (out, _, status) in data[table].items():
                    label, prog, flags, rest, extra = self.MORE_CASE.fullmatch(case).groups()
                    groups.setdefault((label, prog, rest, extra), {})[self.level(flags)] = (out, status, case)
                for (label, prog, rest, extra), levels in groups.items():
                    verb = next(word for word in rest.split() if not word.startswith("-"))
                    base = levels[0][0]
                    for level, (out, status, case) in levels.items():
                        if prog == "apt-cache" or verb == "policy":
                            want = base
                        elif verb in ("list", "search", "show"):
                            want = self.quieter(prog, base, level, answer=False)
                            if verb == "show" and level >= 3:
                                want = self.rule("tpot_apt_show_short")(want)
                        else:
                            want = self.quieter(prog, base, level)
                        with self.subTest(image=image, case=case):
                            self.assertEqual(out, want)
                            self.assertEqual(status, levels[0][1])
                            checked += 1
        self.assertEqual(checked, 600)

    def test_the_unknown_package_answers_are_the_recorded_ones(self):
        checked = 0
        for image, pid in self.IMAGE_PERSONAS.items():
            run = self.session(pid)
            for case, recorded in APT_QUIET["images"][image]["cases"].items():
                if re.fullmatch(r"(?:apt-get|apt) (?:-\S+ )*(?:install|remove) nosuchpkg123", case):
                    with self.subTest(persona=pid, case=case):
                        self.assertEqual(list(run(case)), recorded)
                        checked += 1
        self.assertEqual(checked, 4 * 32)

    def test_the_other_verbs_follow_the_rule_in_the_persona(self):
        # derived from the rule (recorded on a repository of one package), not recorded on the persona's own state
        verbs = ("upgrade", "autoremove", "list --installed curl", "search curl", "show curl")
        for pid in ("debian-bookworm", "debian-trixie", "ubuntu-jammy", "ubuntu-noble", "raspios", "gpu-ubuntu"):
            for verb in verbs:
                prog = "apt-get" if verb in ("upgrade", "autoremove") else "apt"
                base = self.session(pid)(f"{prog} -y {verb}")
                for flags, level in (("-qq", 2), ("-qqq", 3)):
                    with self.subTest(persona=pid, verb=verb, flags=flags):
                        out, err, code = self.session(pid)(f"{prog} {flags} -y {verb}")
                        if verb.split()[0] in ("list", "search", "show"):
                            want = self.quieter(prog, base[0], level, answer=False)
                            if verb.startswith("show") and level == 3:
                                want = self.rule("tpot_apt_show_short")(want)
                        else:
                            want = self.quieter(prog, base[0], level)
                        self.assertEqual((out, err, code), (want, base[1], base[2]))
                        if verb.startswith("list"):
                            self.assertNotIn("Listing...", out)
                        if verb.startswith("search"):
                            self.assertNotIn("Sorting...", out)

    def test_the_personas_with_a_derived_apt_leave_out_the_same(self):
        # raspios (apt 2.6.1 of Debian 12), gpu-ubuntu (Ubuntu 22.04) and the EdgeRouter (apt 1.4.10, recorded on 1.4.11):
        # derived from the rule, not recorded on these images
        for pid, progs in (("raspios", ("apt-get", "apt")), ("gpu-ubuntu", ("apt-get", "apt")),
                           ("ubiquiti-edgerouter-x", ("apt-get",))):
            for prog in progs:
                base = self.session(pid)(f"{prog} -y install curl")
                for flags, level in (("-q", 1), ("-qq", 2), ("-qqq", 3)):
                    with self.subTest(persona=pid, prog=prog, flags=flags):
                        out, err, code = self.session(pid)(f"{prog} {flags} -y install curl")
                        self.assertEqual((out, err, code), (self.quieter(prog, base[0], level), base[1], base[2]))

    def test_a_failed_fetch_leaves_out_the_error_lines_with_the_indented_text(self):
        # synthetic text of a refused fetch (derived, not recorded as such)
        text = ("Ign:1 http://127.0.0.1:9 ./ InRelease\nErr:1 http://127.0.0.1:9 ./ InRelease\n"
                "  Could not connect to 127.0.0.1:9 (127.0.0.1). - connect (111: Connection refused)\n"
                "Reading package lists...\nW: Failed to fetch http://127.0.0.1:9/./InRelease\n")
        self.assertEqual(self.quieter("apt-get", text, 2), "W: Failed to fetch http://127.0.0.1:9/./InRelease\n")
        self.assertEqual(self.quieter("apt-get", text, 1), text)


PROVIDES = json.loads((Path(__file__).parent / "fixtures" / "provides.json").read_text(encoding="utf-8"))
RPM_LIBS = json.loads((Path(__file__).parent / "fixtures" / "rpm_libs.json").read_text(encoding="utf-8"))
APT_RELATIONS = json.loads((Path(__file__).parent / "fixtures" / "apt_relations.json").read_text(encoding="utf-8"))


def stanza_fields(text):
    """[[field, value]] of the first block of a record (continuation lines belong to the value)."""
    found = []
    for line in text.splitlines():
        if not line.strip():
            break
        if line[0] in " \t":
            found[-1][1] += "\n" + line
        else:
            field, _, value = line.partition(": ")
            found.append([field, value])
    return found


class AptRelationsTest(SessionCase):
    """apt show, apt-cache show and dpkg -s print the relations and Homepage of the candidate as the real tools do
    (fixtures/apt_relations.json: stanzas of the pinned indexes served as a repository in debian:12, debian:13,
    ubuntu:22.04, ubuntu:24.04). Left out of the comparison: what a recording of a local repository has of its own
    (Filename, Size, the sums, Download-Size, APT-*; Description-md5 is in a real index, the persona does not print it)
    and the long descriptions."""

    PERSONAS = {"debian_12": "debian-bookworm", "debian_13": "debian-trixie", "ubuntu_22.04": "ubuntu-jammy",
                "ubuntu_24.04": "ubuntu-noble"}
    OWN = {"Filename", "Size", "MD5sum", "SHA1", "SHA256", "SHA512", "Description-md5", "Download-Size",
           "APT-Manual-Installed", "APT-Sources"}
    # the fields of a status entry whose place follows the control file of the package (Ubuntu's differ per package)
    LOOSE = {"Homepage", "Original-Maintainer", "Original-Vcs-Browser", "Original-Vcs-Git", "Build-Essential"}
    # apt-meta has no row for it on any release, so there is nothing to take its relations from
    NO_ROW = {"unattended-upgrades"}
    # a name of the popcon top that no persona models keeps no relation fields (apt-relations.txt.gz stays small)
    NOT_KEPT = {("ubuntu", "ifupdown")}

    def skipped(self, pid, name):
        return name in self.NO_ROW or (pid.split("-")[0], name) in self.NOT_KEPT

    def compare(self, command, drop, in_order=True, loose=False):
        checked = 0
        for image, pid in self.PERSONAS.items():
            run = self.session(pid)
            for case, (recorded, _, status) in APT_RELATIONS["images"][image]["cases"].items():
                if not case.startswith(command):
                    continue
                name = case.split()[-1]
                if self.skipped(pid, name):
                    continue
                out, err, code = run(case)
                if code and command.startswith("dpkg"):
                    run(f"apt-get install -y {name}")
                    out, err, code = run(case)
                want = [f for f in stanza_fields(recorded) if f[0] not in self.OWN | drop]
                got = [f for f in stanza_fields(out) if f[0] not in self.OWN | drop]
                if loose and pid.startswith("ubuntu"):
                    want, got = ([f for f in x if f[0] not in self.LOOSE] + sorted(f for f in x if f[0] in self.LOOSE)
                                 for x in (want, got))
                with self.subTest(persona=pid, case=case):
                    self.assertEqual(code, status, err)
                    self.assertEqual(got if in_order else sorted(got), want if in_order else sorted(want))
                    checked += 1
        return checked

    def test_apt_show_as_recorded(self):
        # the sizes of a recording are those of a stanza that names no file; the service packages' rows of
        #   package_units.SERVICE_META have sizes of their own
        self.assertEqual(self.compare("apt show ", {"Installed-Size"}), 42)

    def test_dpkg_status_as_recorded(self):
        # the installed version of a persona's own package may be older than the candidate the relations name
        self.assertEqual(self.compare("dpkg -s ", {"Version", "Installed-Size"}, loose=True), 42)
        self.assertEqual(self.compare("dpkg-query -s ", {"Version", "Installed-Size"}, loose=True), 42)

    def test_apt_cache_show_as_recorded(self):
        # the order of an Ubuntu index varies per stanza: compared as a set there, derived, not recorded
        self.assertEqual(self.compare("apt-cache show ", {"Installed-Size"}, in_order=False), 42)

    def test_the_order_of_the_debian_index_is_the_recorded_one(self):
        run = self.session("debian-bookworm")
        for case, (recorded, _, _) in APT_RELATIONS["images"]["debian_12"]["cases"].items():
            if case.startswith("apt-cache show ") and case.split()[-1] not in self.NO_ROW:
                with self.subTest(case=case):
                    want = [f[0] for f in stanza_fields(recorded) if f[0] not in self.OWN]
                    self.assertEqual([f[0] for f in stanza_fields(run(case)[0]) if f[0] not in self.OWN], want)


class ProvidesTest(SessionCase):
    """yum provides and dnf provides name every package that has the name, provides it or lists the program
    (fixtures/provides.json: centos:7 and almalinux:9, one package once, the newest build the persona knows)."""

    @staticmethod
    def providers(text):
        """[(package, what matched)] of the blocks of repositories, once each, in the order printed."""
        found = []
        for line in text.splitlines():
            head = re.match(r"(?:\d+:)?(.+?)-(?:\d+:)?\d\S*\.(?:x86_64|noarch) : ", line)
            if head:
                found.append([head.group(1), "", None])
            elif line.startswith("Repo        : "):
                found[-1][1] = line[14:]
            elif line.startswith(("Provides    : ", "Provide    : ", "Filename    : ")):
                found[-1][2] = re.sub(r" = .*", "", line.split(" : ", 1)[1])
        seen = []
        for name, repo, matched in found:
            if not repo.startswith("@") and (name, matched) not in seen:
                seen.append((name, matched))
        return seen

    def test_the_providers_are_the_recorded_ones(self):
        checked = 0
        for image, pid, tool in (("centos:7", "centos-7", "yum"), ("almalinux:9", "rhel-9", "dnf")):
            run = self.session(pid)
            for term, recorded in PROVIDES["images"][image].items():
                if term.startswith("nosuch"):
                    continue
                with self.subTest(image=image, term=term):
                    self.assertEqual(self.providers(run(f"{tool} provides {term}")[0]),
                                     self.providers(recorded["stdout"]))
                    checked += 1
        self.assertEqual(checked, 13)

    def test_yum_names_every_provider_and_the_installed_one_after_them(self):
        run = self.session("centos-7")
        out = run("yum provides MTA")[0]
        self.assertEqual(self.providers(out), [("postfix", "MTA"), ("sendmail", "MTA")])
        self.assertEqual(out.count("Repo        : base\n"), 2)
        self.assertEqual(out.count("Repo        : @anaconda\n"), 1)
        self.assertEqual(self.providers(run("yum provides webserver")[0]), [("httpd", "webserver")])
        self.assertEqual(run("yum provides nosuchterm123")[0].splitlines()[-1], "No matches found")

    def test_dnf_blocks_are_the_recorded_layout(self):
        run = self.session("rhel-9")
        out, err, code = run("dnf provides webserver")
        self.assertEqual((err, code), ("", 0))
        self.assertEqual(self.providers(out), [("httpd", "webserver"), ("nginx", "webserver")])
        self.assertNotIn("\n\n\n", out)
        self.assertTrue(out.endswith("Provide    : webserver\n\n"), out)
        self.assertEqual(out.count("Matched from:\n"), 2)
        self.assertRegex(out, r"(?m)^httpd-[^ ]+\.x86_64 : Apache HTTP Server\nRepo        : \S+\n"
                              r"Matched from:\nProvide    : webserver\n\n")
        # a package's own name is a provide with its version, as the recording of mysql and httpd
        self.assertRegex(run("dnf provides mysql")[0], r"(?m)^Provide    : mysql = \S+\n")
        self.assertEqual(self.providers(run("dnf whatprovides smtpdaemon")[0]),
                         self.providers(run("dnf prov smtpdaemon")[0]))
        self.assertEqual(self.providers(run("dnf provides smtpdaemon")[0]),
                         [("postfix", "smtpdaemon"), ("sendmail", "smtpdaemon")])

    def test_dnf_installed_packages_follow_the_available_ones(self):
        # derived from the yum layout, not recorded: the installed build with the repository @System
        run = self.session("rhel-9")
        run("dnf install -y nginx")
        out = run("dnf provides webserver")[0]
        self.assertIn("Repo        : @System\n", out)
        self.assertEqual(out.count("Repo        : @System\n"), 1)
        self.assertGreater(out.index("Repo        : @System\n"), out.rindex("httpd-"))

    def test_dnf_without_a_match_and_without_a_term(self):
        run = self.session("rhel-9")
        recorded = PROVIDES["images"]["almalinux:9"]["nosuchterm123"]
        out, err, code = run("dnf provides nosuchterm123")
        self.assertEqual((err, code), (recorded["stdout"].splitlines()[-1] + "\n", recorded["rc"]))
        self.assertNotIn("Matched from", out)
        # derived from the arguments of the other commands, not recorded
        self.assertEqual(run("dnf provides")[1:], (
            "usage: dnf provides [-c [config file]] [-q] [-v] [--version] ... PROVIDE [PROVIDE ...]\n"
            "dnf provides: error: the following arguments are required: PROVIDE\n", 2))

    def test_many_glob_terms_end_in_bounded_time(self):
        run = self.session("rhel-9")
        started = time.monotonic()
        out = run("dnf provides " + "a* " * 16000)[0]
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertGreater(len(self.providers(out)), 0)
        # the first globs are answered, those beyond the sixteenth are not
        self.assertEqual(self.providers(run("dnf provides " + "nosuch* " * 16 + "webserver*")[0]), [])

    def test_a_program_glob_finds_every_provider(self):
        self.assertEqual(self.providers(self.session("centos-7")("yum provides /usr/sbin/sendmai?")[0]),
                         [("postfix", "/usr/sbin/sendmail"), ("sendmail", "/usr/sbin/sendmail")])
        self.assertEqual(self.providers(self.session("rhel-9")("dnf provides /usr/sbin/sendmai?")[0]),
                         [("postfix", "/usr/sbin/sendmail"), ("sendmail", "/usr/sbin/sendmail")])


class RpmLibrariesTest(SessionCase):
    """rpm -qf / rpm -ql of the libraries a package brings (the file lists of package_installs.json.gz: the pinned
    filelists of CentOS 7.9.2009 and AlmaLinux 9.4) and of the loader of the tree."""

    def test_a_library_of_an_installed_package_has_its_owner(self):
        for pid, package, owner, paths in (
                ("centos-7", "httpd", "httpd", ("/usr/lib64/httpd/modules/mod_rewrite.so",
                                                "/usr/lib64/httpd/modules/mod_proxy.so")),
                ("centos-7", "mariadb-server", "mariadb-server", ("/usr/lib64/mysql/plugin/auth_socket.so",)),
                ("rhel-9", "httpd", "httpd-core", ("/usr/lib64/httpd/modules/mod_rewrite.so",))):
            run = self.session(pid)
            for path in paths:
                self.assertEqual(run(f"rpm -qf {path}")[1:], (f"error: file {path}: No such file or directory\n", 1))
            self.assertEqual(run(f"{'yum' if pid == 'centos-7' else 'dnf'} install -y {package}")[2], 0)
            for path in paths:
                with self.subTest(persona=pid, path=path):
                    out, err, code = run(f"rpm -qf {path}")
                    self.assertRegex(out, rf"^{owner}-\d\S*\.x86_64\n$")
                    self.assertEqual((err, code), ("", 0))
                    self.assertIn(path + "\n", run(f"rpm -ql {owner}")[0])
            if owner == package:
                self.assertEqual(run(f"rpm -e {package}")[2], 0)
                self.assertEqual(run(f"rpm -qf {paths[0]}")[2], 1)

    def test_the_loader_of_the_tree_is_glibc(self):
        for pid, paths in (("centos-7", ("/usr/lib64/ld-2.17.so", "/usr/lib64/ld-linux-x86-64.so.2",
                                         "/lib64/ld-linux-x86-64.so.2")),
                           ("rhel-9", ("/usr/lib64/ld-linux-x86-64.so.2", "/lib64/ld-linux-x86-64.so.2"))):
            run = self.session(pid)
            for path in paths:
                with self.subTest(persona=pid, path=path):
                    out, err, code = run(f"rpm -qf {path}")
                    self.assertRegex(out, r"^glibc-2\.\d+-\S+\.x86_64\n$")
                    self.assertEqual((err, code), ("", 0))

    RECORDED = {"centos:7": "centos-7", "almalinux:9": "rhel-9"}

    def test_rpm_qf_of_the_recorded_library_paths_names_the_recorded_owner(self):
        # the data is the pinned CentOS 7.9.2009 / AlmaLinux 9.4 vault; the recorded almalinux:9 is 9.8 (glibc-2.34-283.el9_8,
        # openssl-libs-3.5.8-2.el9_8), so the builds differ and only the package name is compared
        checked = 0
        for image, pid in self.RECORDED.items():
            run = self.session(pid)
            for path, recorded in RPM_LIBS["images"][image]["qf"].items():
                if path == "/etc/hostname":
                    continue
                out, err, code = run(f"rpm -qf {path}")
                with self.subTest(persona=pid, path=path):
                    self.assertEqual(code, recorded["rc"])
                    self.assertEqual(run.protocol.fs.exists(path), code == 0 or "not owned" in out)
                    if code:
                        self.assertEqual(err, recorded["out"] + "\n")
                    else:
                        self.assertEqual(re.sub(r"-\d.*", "", out), re.sub(r"-\d.*", "", recorded["out"]) + "\n")
                    checked += 1
        self.assertEqual(checked, 38)

    RELEASES = {"7": "centos:7", "9.4": "almalinux:9.4", "9.5": "almalinux:9.5", "9.6": "almalinux:9.6"}

    def test_the_library_table_is_the_recorded_stat_of_each_release(self):
        checked = 0
        for release, image in self.RELEASES.items():
            recorded = RPM_LIBS["stat"][image]["files"]
            for package, files in system.RPM_LIBRARIES[release].items():
                for name, value in files.items():
                    with self.subTest(image=image, name=name):
                        have = recorded[f"/usr/lib64/{name}"]
                        self.assertEqual(have["package"], package)
                        if isinstance(value, str):
                            self.assertEqual((have["type"], have["target"]), ("link", value))
                        else:
                            self.assertEqual((have["type"], have["size"], have["mode"]), ("file", value, "755"))
                        checked += 1
        self.assertEqual(checked, 26 + 3 * 16)

    def test_the_tree_has_links_and_files_as_recorded(self):
        for pid, release in (("centos-7", "7"), ("rhel-9", "9.5")):
            run = self.session(pid)
            fs = run.protocol.fs
            self.assertEqual(run.ident.release.os_version_id, {"7": "7", "9.5": "9.5"}[release])
            for files in system.RPM_LIBRARIES[release].values():
                for name, value in files.items():
                    path = f"/usr/lib64/{name}"
                    node = fs.getfile(path, follow_symlinks=False)
                    with self.subTest(persona=pid, path=path):
                        self.assertIsNotNone(node)
                        if isinstance(value, str):
                            self.assertEqual((node[5], node[8]), (0o120777, value))
                            self.assertTrue(fs.exists(path))
                        else:
                            self.assertEqual((node[5], node[4], node[8]), (0o100755, value, None))
            # the size of a link is the length of its target, as stat shows it (refs rpm_libs.json "stat")
            recorded = RPM_LIBS["stat"]["centos:7" if pid == "centos-7" else "almalinux:9.5"]["files"]
            for path in ("/usr/lib64/libc.so.6", "/usr/lib64/libz.so.1"):
                self.assertEqual(fs.getfile(path, follow_symlinks=False)[4], recorded[path]["size"])

    def test_a_file_that_exists_and_no_package_owns_is_not_owned(self):
        for pid in ("centos-7", "rhel-9"):
            run = self.session(pid)
            self.assertEqual(run("rpm -qf /root")[1:], ("", 1))
            self.assertEqual(run("rpm -qf /root")[0], "file /root is not owned by any package\n")

    def test_the_library_lists_are_the_recorded_ones(self):
        # centos:7 is the pinned release; almalinux:9 (9.8) is later than the pinned 9.4, so only the packages whose
        # libraries did not move between the two
        with gzip.open(system.NAMES_DIR.parent / "package_installs.json.gz", "rt") as f:
            installs = json.load(f)
        for image, family, packages in (("centos:7", "centos", ("httpd", "mariadb-server")),
                                        ("almalinux:9", "rhel", ("httpd", "httpd-core", "postfix"))):
            for package in packages:
                with self.subTest(image=image, package=package):
                    have = sorted(path for path in installs[family]["files"][package]
                                  if re.fullmatch(r"/(usr/)?lib(64)?/\S+\.so(\.[0-9]+)*", path))
                    self.assertEqual(have, RPM_LIBS["images"][image]["libs"][package])


if __name__ == "__main__":
    unittest.main()
