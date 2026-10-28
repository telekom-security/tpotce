"""The builder of the package lists (docker/cowrie/tools/build_package_names.py) on small indexes."""
import contextlib
import gzip
import hashlib
import importlib.util
import io
import lzma
import re
import unittest
import urllib.error
from pathlib import Path

from tests.helpers import tmpdir

TOOL = Path(__file__).resolve().parents[1] / "tools" / "build_package_names.py"


def builder():
    spec = importlib.util.spec_from_file_location("build_package_names", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stanza(name, version, section="utils", isize="113", size="52464", description="a tool", **more):
    fields = {"Package": name, "Version": version, "Installed-Size": isize, "Architecture": "amd64", "Maintainer": "X",
              "Priority": "optional",
              "Description": description, "Section": section, "Size": size, **more}
    return "".join(f"{key}: {value}\n" for key, value in fields.items()) + "\n"


class BuilderTest(unittest.TestCase):
    def setUp(self):
        self.b = builder()

    def test_debian_versions_compare_as_dpkg_does(self):
        cmp = self.b.deb_compare
        for low, high in (("1.0-1", "1.0-2"), ("1.0~rc1-1", "1.0-1"), ("2.1.1-2ubuntu3", "2.1.1-2ubuntu3.22.04.9"),
                          ("9", "1:1"), ("1.2.3", "1.2.3a"), ("1.0-1", "1.0-1+b1"), ("5.36.0-7+deb12u3",
                                                                                     "5.36.0-7+deb12u4"),
                          ("1.9", "1.10"), ("1.0", "1.0.0"), ("a", "b"), ("1.0~", "1.0")):
            with self.subTest(low=low, high=high):
                self.assertEqual(cmp(low, high), -1)
                self.assertEqual(cmp(high, low), 1)
        self.assertEqual(cmp("0:1.0-1", "1.0-1"), 0)
        # a digit against a letter is 0 in dpkg's order; the epoch ends at the first colon
        self.assertEqual(cmp("1.0", "1.a"), -1)
        self.assertEqual(cmp("1:2:3-1", "1:2:4-1"), -1)

    def test_apt_meta_keeps_the_service_packages_outside_the_popcon_top(self):
        # popcon ranks: the service names are not in the popcon file here, the other name is above META_TOP
        root = tmpdir(self)
        ranks = [f"{rank} {name} {rank * 10}" for rank, name in ((1, "bash"), (self.b.META_TOP + 1, "zz-unranked"))]
        (root / "popcon-by_inst.gz").write_bytes(gzip.compress("\n".join(ranks).encode()))
        top = self.b.apt_meta_names(root)
        for name in self.b.SERVICES:
            with self.subTest(name=name):
                self.assertIn(name, top)
        self.assertIn("bash", top)
        self.assertNotIn("zz-unranked", top)

    def test_apt_relations_are_kept_for_the_modelled_packages_only(self):
        top = self.b.apt_relation_names(Path("unused"))
        self.assertTrue(set(self.b.SERVICES) <= top and set(self.b.EXTRA) <= top and self.b.modelled() <= top)
        self.assertEqual(top, set(self.b.EXTRA) | set(self.b.SERVICES) | self.b.modelled())

    def test_apt_meta_takes_the_candidate_of_each_release(self):
        root = tmpdir(self)
        indexes = {
            "rel.xz": stanza("tree", "2.1.1-2ubuntu3", "universe/utils", description="displays a tree")
            + stanza("bash", "5.2-1", "shells", description="GNU Bourne Again SHell"),
            "upd.xz": stanza("tree", "2.1.1-2ubuntu3.22.04.9", "universe/utils", size="47360",
                             description="displays a tree") + stanza("libssl3", "3.0.2-1.30", "libs"),
            "sec.xz": stanza("libssl3", "3.0.2-1.30", "libs") + stanza("bash", "5.2-1.1", "shells",
                                                                           description="GNU Bourne Again SHell"),
            "bpo.xz": stanza("bash", "5.3-1", "shells") + stanza("newtool", "1.0-1", "net"),
        }
        for name, text in indexes.items():
            (root / name).write_bytes(lzma.compress(text.encode()))
        files = {"rel.xz": "-", "upd.xz": "updates", "bpo.xz": "backports", "sec.xz": "security"}
        rows = self.b.apt_meta({"x": [root / name for name in files]}, {"x": {"tree", "bash", "libssl3", "newtool"}},
                               lambda path: files[path.name])
        self.assertEqual(rows, [
            "bash\tx\t5.2-1.1\tmain\tsecurity\t113\t52464\t-\tshells\tX\toptional\t-\tGNU Bourne Again SHell",
            "libssl3\tx\t3.0.2-1.30\tmain\tupdates\t113\t52464\t-\tlibs\tX\toptional\t-\ta tool",
            "newtool\tx\t1.0-1\tmain\tbackports\t113\t52464\t-\tnet\tX\toptional\t-\ta tool",
            "tree\tx\t2.1.1-2ubuntu3.22.04.9\tuniverse\tupdates\t113\t47360\t-\tutils\tX\toptional\t-\tdisplays a tree"])

    def test_apt_relations_take_the_fields_of_the_candidate_of_apt_meta(self):
        root = tmpdir(self)
        text = (stanza("tree", "2.1.0-1", Depends="libc6 (>= 2.34)", Homepage="http://x.example/",
                       Tag="role::program,\n use::browsing")
                + stanza("bash", "5.2-1", **{"Pre-Depends": "libc6", "Original-Maintainer": "Y", "Build-Essential": "yes"})
                + stanza("plain", "1.0-1"))
        (root / "rel.xz").write_bytes(lzma.compress(text.encode()))
        indexes, keep = {"x": [root / "rel.xz"]}, {"x": {"tree", "bash", "plain"}}
        rows = self.b.apt_relations(indexes, keep, lambda path: "-")
        fields = self.b.RELATION_FIELDS
        self.assertEqual(len(rows), 2)
        for row, name, version, values in (
                (rows[0], "bash", "5.2-1", {"Pre-Depends": "libc6", "Original-Maintainer": "Y", "Build-Essential": "yes"}),
                (rows[1], "tree", "2.1.0-1", {"Depends": "libc6 (>= 2.34)", "Homepage": "http://x.example/",
                                              "Tag": "role::program,\\n use::browsing"})):
            self.assertEqual(row.split("\t"), [name, "x", version, *(values.get(field, "") for field in fields)])

    def test_apt_meta_keeps_the_short_description_priority_and_essential(self):
        root = tmpdir(self)
        text = stanza("bash", "5.2.15-2+b7", "shells", Priority="required", Essential="yes",
                      description="GNU Bourne Again SHell\n Bash is an sh-compatible command language interpreter.")
        (root / "rpi.gz").write_bytes(gzip.compress(text.encode()))
        rows = self.b.apt_meta({"r": [root / "rpi.gz"]}, {"r": {"bash"}}, lambda path: "rpi")
        self.assertEqual(rows, ["bash\tr\t5.2.15-2+b7\tmain\trpi\t113\t52464\t-\tshells\tX\trequired\tyes\t"
                                "GNU Bourne Again SHell"])
        self.assertEqual(self.b.base_names({"r": [root / "rpi.gz"]}), {"r": {"bash"}})

    def test_the_main_component_of_debian_is_taken_whole(self):
        # every name of main: hello ranks below the popcon cut and is in the 30 names of the ref d13-09
        for letter in ("b", "t", "r"):
            whole, cut = self.b.APT_RELEASES[letter]
            self.assertTrue(all("-main-" in name or name.startswith("rpi-") for name in whole), letter)
            self.assertFalse([name for name in cut if "-main-" in name], letter)
        for letter in ("j", "n"):
            whole, cut = self.b.APT_RELEASES[letter]
            self.assertTrue(all("-main-" in name or "-restricted-" in name for name in whole), letter)

    def test_deb_base_is_the_base_of_the_index_and_of_the_image(self):
        root = tmpdir(self)
        (root / "deb-base-b.txt").write_text("libc6\nbash\n")
        self.assertEqual(self.b.deb_base(root, {"b": {"bash", "grep"}, "r": {"bash"}}),
                         ["bash br", "grep b", "libc6 br"])

    def test_deb_files_of_the_images(self):
        root = tmpdir(self)
        (root / "deb-files-b.txt").write_text("/bin/ls coreutils\n/usr/bin/du coreutils\n")
        (root / "deb-files-t.txt").write_text("/usr/bin/ls coreutils\n/usr/bin/du coreutils\n")
        self.assertEqual(self.b.deb_files(root, ("b", "t")), ["/bin/ls b coreutils", "/usr/bin/du bt coreutils",
                                                             "/usr/bin/ls t coreutils"])

    def test_apt_meta_joins_the_releases_of_one_row(self):
        root = tmpdir(self)
        (root / "a.xz").write_bytes(lzma.compress(stanza("tree", "2.1.0-1").encode()))
        (root / "b.xz").write_bytes(lzma.compress(stanza("tree", "2.1.0-1").encode()))
        rows = self.b.apt_meta({"b": [root / "a.xz"], "r": [root / "b.xz"]}, {"b": {"tree"}, "r": {"tree"}},
                               lambda path: "-")
        self.assertEqual(rows, ["tree\tbr\t2.1.0-1\tmain\t-\t113\t52464\t-\tutils\tX\toptional\t-\ta tool"])

    def test_opkg_meta_has_the_version_and_description_of_each_feed(self):
        root = tmpdir(self)
        text = ("Package: tree\nVersion: 2.0.2-1\nDepends: libc, libgcc1\nSection: utils\nSize: 25178\n"
                "Description:  List contents of directories in a tree-like format\n\n"
                "Package: acl\nVersion: 2.3.1-1\nDepends: libc\nSection: utils\nSize: 9000\n"
                "Description:  Access control list support\n This package provides ACL manipulation utilities\n"
                " - chacl\n\n")
        (root / "ow-22.03.7-packages.gz").write_bytes(gzip.compress(text.encode()))
        (root / "ow-21.02.7-packages.gz").write_bytes(gzip.compress(text.replace("2.0.2-1", "1.8.0-1").encode()))
        rows = self.b.opkg_meta({"a": [root / "ow-21.02.7-packages.gz"], "b": [root / "ow-22.03.7-packages.gz"]})
        self.assertEqual(rows, [
            "acl\tab\t2.3.1-1\tAccess control list support This package provides ACL manipulation utilities - chacl",
            "tree\ta\t1.8.0-1\tList contents of directories in a tree-like format",
            "tree\tb\t2.0.2-1\t"])

    def test_dnf_names_of_the_vault_repositories(self):
        root = tmpdir(self)
        ns = 'xmlns="http://linux.duke.edu/metadata/common" xmlns:rpm="http://linux.duke.edu/metadata/rpm"'

        def package(name, provides=(), files=()):
            entries = "".join(f'<rpm:entry name="{p}"/>' for p in provides)
            listed = "".join(f"<file>{f}</file>" for f in files)
            return (f'<package type="rpm"><name>{name}</name><arch>x86_64</arch><format><rpm:provides>{entries}'
                    f"</rpm:provides>{listed}</format></package>")

        xml = (f"<metadata {ns}>" + package("tree") + package("postfix", ["smtpdaemon", "MTA"], ["/usr/sbin/sendmail"])
               + package("sendmail", ["smtpdaemon"]) + package("s-nail", ["mailx"], ["/usr/bin/mail"])
               + package("almalinux-release", ["system-release"]) + package("lorax-templates-almalinux")
               + "</metadata>")
        (root / "el9-BaseOS-primary.xml.gz").write_bytes(gzip.compress(xml.encode()))
        rows = self.b.dnf_names({"baseos": root / "el9-BaseOS-primary.xml.gz"})
        self.assertEqual(rows, ["lorax-templates-rhel baseos", "postfix baseos", "redhat-release baseos",
                                "s-nail baseos", "sendmail baseos", "tree baseos", "/usr/bin/mail > s-nail",
                                "/usr/sbin/sendmail > postfix", "MTA > postfix", "mailx > s-nail",
                                "smtpdaemon > postfix,sendmail", "system-release > redhat-release"])
        xml = xml.replace("</metadata>", package("lorax-x", ["almalinux-release"]) + "</metadata>")
        (root / "el9-BaseOS-primary.xml.gz").write_bytes(gzip.compress(xml.encode()))
        self.assertNotIn("almalinux-release > lorax-x", self.b.dnf_names({"baseos": root / "el9-BaseOS-primary.xml.gz"}))

    def test_dnf_meta_names_the_builds_of_red_hat(self):
        rows = ["almalinux-release\tx86_64\t0:9.4-1.el9\tbaseos\t1\t2\tAlmaLinux release files\thttps://almalinux.org"
                "\tGPLv2\tUnspecified\t\talmalinux-release-9.4-1.el9.src.rpm",
                "nginx\tx86_64\t1:1.24.0-1.module_el9.4.0+106+015ce7f8.1.alma.1\tappstream\t1\t2\tA web server\t"
                "https://nginx.org\tBSD\tUnspecified\tnginx for AlmaLinux\t"
                "nginx-1.24.0-1.module_el9.4.0+106+015ce7f8.1.alma.1.src.rpm",
                "kernel-abi-stablelists\tnoarch\t0:5.14.0-427.42.1.el9_4\tbaseos\t1\t2\tThe AlmaLinux kernel ABI "
                "symbol stablelists\thttps://www.kernel.org/\tGPLv2\tUnspecified\t\tkernel-5.14.0-427.42.1.el9_4.src.rpm"]
        self.assertEqual(self.b.rhel_rows(rows), [
            "nginx\tx86_64\t1:1.24.0-1.module+el9.4.0+106+015ce7f8.1\tappstream\t1\t2\tA web server\t"
            "https://nginx.org\tBSD\tUnspecified\tnginx for Red Hat Enterprise Linux\t"
            "nginx-1.24.0-1.module+el9.4.0+106+015ce7f8.1.src.rpm",
            "kernel-abi-stablelists\tnoarch\t0:5.14.0-427.42.1.el9_4\tbaseos\t1\t2\tThe Red Hat Enterprise Linux "
            "kernel ABI symbol stablelists\thttps://www.kernel.org/\tGPLv2\tUnspecified\t\t"
            "kernel-5.14.0-427.42.1.el9_4.src.rpm"])
        self.assertFalse(any("alma" in row.lower() for row in self.b.rhel_rows(rows)))

    def test_yum_meta_takes_the_newest_build(self):
        root = tmpdir(self)
        ns = 'xmlns="http://linux.duke.edu/metadata/common" xmlns:rpm="http://linux.duke.edu/metadata/rpm"'

        def package(name, arch, epoch, ver, rel, summary="a tool"):
            return (f'<package type="rpm"><name>{name}</name><arch>{arch}</arch><version epoch="{epoch}" '
                    f'ver="{ver}" rel="{rel}"/><summary>{summary}</summary><description>The tree utility '
                    f'recursively\ndisplays directories.</description><url>http://x/{name}</url>'
                    '<size package="45" installed="83"/><format><rpm:license>GPLv3+</rpm:license>'
                    "<rpm:group>System Environment/Shells</rpm:group>"
                    f"<rpm:sourcerpm>{name}-src.rpm</rpm:sourcerpm></format></package>")

        (root / "os.xml.gz").write_bytes(gzip.compress((f"<metadata {ns}>" + package("tree", "x86_64", 0, "1.6.0",
                                                                                     "10.el7", "File system tree viewer")
                                                       + package("bash", "x86_64", 0, "4.2.46", "34.el7")
                                                       + package("glibc", "i686", 0, "2.17", "317.el7")
                                                       + "</metadata>").encode()))
        (root / "upd.xml.gz").write_bytes(gzip.compress((f"<metadata {ns}>" + package("bash", "x86_64", 0, "4.2.46",
                                                                                      "35.el7_9")
                                                        + package("tree", "x86_64", 0, "1.6.0", "9.el7")
                                                        + "</metadata>").encode()))
        self.assertEqual(self.b.yum_meta({"base": root / "os.xml.gz", "updates": root / "upd.xml.gz"}, {"tree"}), [
            "bash\tx86_64\t0:4.2.46-35.el7_9\tupdates\t45\t83\ta tool\thttp://x/bash\tGPLv3+\tSystem Environment/Shells\t"
            "\tbash-src.rpm",
            "tree\tx86_64\t0:1.6.0-10.el7\tbase\t45\t83\tFile system tree viewer\thttp://x/tree\tGPLv3+\t"
            "System Environment/Shells\tThe tree utility recursively\\ndisplays directories.\ttree-src.rpm"])
        self.assertEqual(self.b.rpm_compare((0, "1.10", "1"), (0, "1.9", "1")), 1)
        self.assertEqual(self.b.rpm_compare((0, "1.0", "1.el7"), (0, "1.0", "1.el7_9")), -1)


    def test_the_builds_of_a_module_stream_are_left_out(self):
        """RHEL 9 enables no module stream by default: dnf installs the build outside the streams, a name only a
        stream has is not found (almalinux:9.4 with the 9.4 vault, refs t9.json el94-dnf-*)."""
        root = tmpdir(self)
        ns = 'xmlns="http://linux.duke.edu/metadata/common" xmlns:rpm="http://linux.duke.edu/metadata/rpm"'

        def package(name, epoch, ver, rel, files=()):
            listed = "".join(f"<file>{path}</file>" for path in files)
            return (f'<package type="rpm"><name>{name}</name><arch>x86_64</arch><version epoch="{epoch}" '
                    f'ver="{ver}" rel="{rel}"/><summary>a tool</summary><url>http://x/{name}</url>'
                    '<size package="45" installed="83"/><format><rpm:license>BSD</rpm:license>'
                    f"<rpm:sourcerpm>{name}-src.rpm</rpm:sourcerpm>{listed}</format></package>")

        (root / "as.xml.gz").write_bytes(gzip.compress((
            f"<metadata {ns}>" + package("nginx", 1, "1.24.0", "1.module_el9.4.0+106+015ce7f8.1.alma.1")
            + package("nginx", 1, "1.20.1", "16.el9_4.1.alma.1")
            + package("postgresql-server-devel", 0, "15.6", "1.module_el9.4.0+92+8ec2cd5a", ("/usr/bin/pg_server_config",))
            + "</metadata>").encode()))
        rows = self.b.rhel_rows(self.b.yum_meta({"appstream": root / "as.xml.gz"}))
        self.assertEqual([row.split("\t")[:3] for row in rows], [["nginx", "x86_64", "1:1.20.1-16.el9_4.1"]])
        self.assertEqual(self.b.dnf_names({"appstream": root / "as.xml.gz"}), ["nginx appstream"])

    def test_a_marker_in_the_description_keeps_the_row(self):
        """gnupg2 describes its "design philosophy": the row stays, without the description."""
        root = tmpdir(self)
        ns = 'xmlns="http://linux.duke.edu/metadata/common" xmlns:rpm="http://linux.duke.edu/metadata/rpm"'
        xml = (f'<metadata {ns}><package type="rpm"><name>gnupg2</name><arch>x86_64</arch>'
               '<version epoch="0" ver="2.3.3" rel="4.el9"/><summary>Utility for secure communication</summary>'
               '<description>It has a different design philosophy.</description><size package="1" installed="2"/>'
               '<format><rpm:sourcerpm>gnupg2-src.rpm</rpm:sourcerpm></format></package></metadata>')
        (root / "b.xml.gz").write_bytes(gzip.compress(xml.encode()))
        rows = self.b.yum_meta({"baseos": root / "b.xml.gz"}, {"gnupg2"})
        self.assertEqual([row.split("\t")[10] for row in rows], [""])
        self.assertEqual(self.b.write(root / "out.gz", rows)[0], 1)

    def test_rpm_manuals_are_the_manual_pages_of_the_programs(self):
        """Only the manual pages (sections 1 and 8) of a package's own programs, as its filelists name them; the
        AlmaLinux brand names as those of Red Hat."""
        root = tmpdir(self)
        ns = 'xmlns="http://linux.duke.edu/metadata/filelists"'

        def package(name, *files):
            return f'<package pkgid="x" name="{name}" arch="x86_64">' + "".join(
                f"<file>{f}</file>" for f in files) + "</package>"

        for name, packages in (("c7.xml.gz", package("tree", "/usr/bin/tree", "/usr/share/doc/tree-1.6.0/README",
                                                     "/usr/share/man/man1/tree.1.gz")
                                + package("glibc", "/usr/sbin/glibc_post_upgrade.x86_64", "/usr/sbin/iconvconfig",
                                          "/usr/share/man/man5/nsswitch.conf.5.gz")
                                + package("postfix", "/usr/sbin/postfix", "/usr/share/man/man1/postfix.1.gz",
                                          "/usr/share/man/man8/postfix.8.gz", "/usr/share/man/man1/mailq.1.gz")),
                               ("el9.xml.gz", package("tree", "/usr/bin/tree", "/usr/share/man/man1/tree.1.gz")
                                + package("almalinux-release", "/usr/bin/x", "/usr/share/man/man1/x.1.gz"))):
            (root / name).write_bytes(gzip.compress(f"<filelists {ns}>{packages}</filelists>".encode()))
        self.assertEqual(self.b.rpm_manuals({"c": [root / "c7.xml.gz"], "r": [root / "el9.xml.gz"]}), [
            "/usr/share/man/man1/postfix.1.gz c postfix", "/usr/share/man/man1/tree.1.gz cr tree",
            "/usr/share/man/man1/x.1.gz r redhat-release", "/usr/share/man/man8/postfix.8.gz c postfix"])

    def test_rows_with_a_marker_of_the_validator_are_left_out(self):
        out = tmpdir(self) / "x.txt.gz"
        self.assertEqual(self.b.write(out, ["tree\ta", "kmod-x\tKernel module for Philips chips", "y svr04"])[0], 1)
        self.assertEqual(gzip.decompress(out.read_bytes()), b"tree\ta\n")


class FakeWeb:
    """urlopen of the fetch: {url: bytes}; nothing else is reachable."""

    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else request
        self.asked.append(url)
        if url not in self.pages:
            raise urllib.error.URLError(f"no route to {url}")
        return io.BytesIO(self.pages[url])


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class SourcesTest(unittest.TestCase):
    """The inputs of the builder are pinned: a URL of an archive of a day (or of a release that does not change)
    and the sha256 of the file; they are fetched into a cache and verified, never read from /tmp."""

    def setUp(self):
        self.b = builder()
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def test_sources_are_pinned_with_a_hash(self):
        self.assertTrue(self.b.SOURCES)
        names = [source.name for source in self.b.SOURCES]
        self.assertEqual(len(names), len(set(names)))
        for source in self.b.SOURCES:
            with self.subTest(source=source.name):
                self.assertRegex(source.sha256, r"^[0-9a-f]{64}$")
                self.assertTrue(source.url.startswith("https://"), source.url)
                if source.name in self.b.MOVING:
                    # no archive of this file exists: the pin is the hash of the build, a newer file is refused
                    self.assertTrue(self.b.MOVING[source.name])
                elif source.name.startswith(("deb-", "ub-")):
                    # the archive as it was at a time: snapshot.debian.org / snapshot.ubuntu.com, the time in the URL
                    self.assertRegex(source.url, r"^https://snapshot\.(debian\.org/archive/debian(-security)?|ubuntu\.com/ubuntu)"
                                                 r"/20\d{6}T\d{6}Z/dists/")
                elif source.name.startswith("ow-"):
                    self.assertRegex(source.url, r"^https://downloads\.openwrt\.org/releases/2\d\.\d\d\.\d/")
                else:
                    # the vaults of the old releases, the file named by its own sha256
                    self.assertRegex(source.url, rf"^https://vault\.(centos|almalinux)\.org/.*/repodata/{source.sha256}-"
                                                 r"(primary|filelists)\.xml\.gz$")
        # the timestamps are constants the README names
        readme = (TOOL.parents[1] / "dist" / "tpot_personas" / "package_names" / "README.md").read_text()
        for stamp in self.b.SNAPSHOTS.values():
            self.assertRegex(stamp, r"^20\d{6}T\d{6}Z$")
            self.assertIn(stamp, readme)
        self.assertEqual(set(self.b.MOVING), {"popcon-by_inst.gz", "rpi-bookworm-main-arm64.gz"})

    def test_every_input_of_the_builder_is_a_source(self):
        pinned = {source.name for source in self.b.SOURCES}
        needed = set(self.b.input_names())
        self.assertEqual(needed - pinned, set())
        self.assertEqual(pinned - needed, set())
        self.assertIn("popcon-by_inst.gz", needed)
        self.assertIn("ub-noble-security-universe-amd64.xz", needed)
        self.assertIn("el94-AppStream-filelists.xml.gz", needed)
        self.assertIn("ow-22.03.7-core.gz", needed)

    def test_the_url_of_each_kind_of_file(self):
        url = self.b.source_url
        deb, sec, ub = (self.b.SNAPSHOTS[k] for k in ("debian", "debian-security", "ubuntu"))
        self.assertEqual(url("deb-bookworm-main-amd64.xz", "0" * 64),
                         f"https://snapshot.debian.org/archive/debian/{deb}/dists/bookworm/main/binary-amd64/Packages.xz")
        self.assertEqual(url("deb-trixie-backports-main-amd64.xz", "0" * 64),
                         f"https://snapshot.debian.org/archive/debian/{deb}/dists/trixie-backports/main/binary-amd64/"
                         "Packages.xz")
        self.assertEqual(url("deb-bookworm-security-non-free-firmware-arm64.xz", "0" * 64),
                         f"https://snapshot.debian.org/archive/debian-security/{sec}/dists/bookworm-security/"
                         "non-free-firmware/binary-arm64/Packages.xz")
        self.assertEqual(url("ub-jammy-updates-restricted-amd64.xz", "0" * 64),
                         f"https://snapshot.ubuntu.com/ubuntu/{ub}/dists/jammy-updates/restricted/binary-amd64/Packages.xz")
        self.assertEqual(url("ow-21.02.7-core.gz", "0" * 64), "https://downloads.openwrt.org/releases/21.02.7/targets/"
                                                              "ramips/mt7621/packages/Packages.gz")
        self.assertEqual(url("ow-22.03.7-luci.gz", "0" * 64), "https://downloads.openwrt.org/releases/22.03.7/packages/"
                                                              "mipsel_24kc/luci/Packages.gz")
        self.assertEqual(url("c7-os-primary.xml.gz", "ab" * 32),
                         f"https://vault.centos.org/7.9.2009/os/x86_64/repodata/{'ab' * 32}-primary.xml.gz")
        self.assertEqual(url("el94-AppStream-filelists.xml.gz", "cd" * 32),
                         f"https://vault.almalinux.org/9.4/AppStream/x86_64/os/repodata/{'cd' * 32}-filelists.xml.gz")
        with self.assertRaises(ValueError):
            url("something.txt", "0" * 64)

    def test_the_cache_is_not_tmp(self):
        self.assertEqual(self.b.cache_dir({"XDG_CACHE_HOME": "/x/cache"}), Path("/x/cache/tpot-cowrie/apt"))
        self.assertEqual(self.b.cache_dir({"HOME": "/home/u"}), Path("/home/u/.cache/tpot-cowrie/apt"))
        # an empty or relative XDG_CACHE_HOME is no cache folder (the specification)
        self.assertEqual(self.b.cache_dir({"XDG_CACHE_HOME": "", "HOME": "/home/u"}),
                         Path("/home/u/.cache/tpot-cowrie/apt"))
        self.assertEqual(self.b.cache_dir({"XDG_CACHE_HOME": "cache", "HOME": "/home/u"}),
                         Path("/home/u/.cache/tpot-cowrie/apt"))
        self.assertFalse(str(self.b.cache_dir({"HOME": "/home/u"})).startswith("/tmp"))

    def sources(self, **files):
        """Sources of small files: {name: bytes}; their URLs are served by a FakeWeb."""
        sources = tuple(self.b.Source(name, f"https://example.org/{name}", sha256(data)) for name, data in files.items())
        return sources, FakeWeb({f"https://example.org/{name}": data for name, data in files.items()})

    def test_fetch_downloads_into_the_cache_and_checks_the_hash(self):
        cache = tmpdir(self) / "cache" / "apt"
        sources, web = self.sources(**{"a.xz": b"alpha", "b.gz": b"beta"})
        got = self.b.fetch(cache, sources=sources, opener=web)
        self.assertEqual(sorted(path.name for path in got), ["a.xz", "b.gz"])
        self.assertEqual((cache / "a.xz").read_bytes(), b"alpha")
        self.assertEqual(sorted(path.name for path in cache.iterdir()), ["a.xz", "b.gz"])    # no .part left
        # a second run needs no network
        again = FakeWeb({})
        self.b.fetch(cache, sources=sources, opener=again)
        self.assertEqual(again.asked, [])

    def test_fetch_refuses_a_file_that_does_not_match_its_hash(self):
        cache = tmpdir(self) / "cache"
        sources, web = self.sources(**{"a.xz": b"alpha"})
        web.pages["https://example.org/a.xz"] = b"changed"
        with self.assertRaises(self.b.FetchError) as raised:
            self.b.fetch(cache, sources=sources, opener=web)
        self.assertIn("a.xz", str(raised.exception))
        self.assertIn(sha256(b"alpha"), str(raised.exception))
        self.assertIn(sha256(b"changed"), str(raised.exception))
        # nothing of it stays in the cache
        self.assertEqual(list(cache.iterdir()), [])

    def test_fetch_replaces_a_damaged_file_of_the_cache(self):
        cache = tmpdir(self)
        (cache / "a.xz").write_bytes(b"half")
        sources, web = self.sources(**{"a.xz": b"alpha"})
        self.b.fetch(cache, sources=sources, opener=web)
        self.assertEqual((cache / "a.xz").read_bytes(), b"alpha")

    def test_offline_uses_only_the_cache(self):
        cache = tmpdir(self)
        sources, web = self.sources(**{"a.xz": b"alpha", "b.gz": b"beta"})
        (cache / "a.xz").write_bytes(b"alpha")
        with self.assertRaises(self.b.FetchError) as raised:
            self.b.fetch(cache, sources=sources, offline=True, opener=web)
        self.assertIn("b.gz", str(raised.exception))
        self.assertIn("--fetch", str(raised.exception))
        self.assertEqual(web.asked, [])
        (cache / "b.gz").write_bytes(b"beta")
        got = self.b.fetch(cache, sources=sources, offline=True, opener=web)
        self.assertEqual(sorted(path.name for path in got), ["a.xz", "b.gz"])
        # a cached file with another hash is no input either
        (cache / "b.gz").write_bytes(b"tampered")
        with self.assertRaises(self.b.FetchError) as raised:
            self.b.fetch(cache, sources=sources, offline=True, opener=web)
        self.assertIn("b.gz", str(raised.exception))
        self.assertEqual(web.asked, [])

    def test_offline_makes_no_folder(self):
        cache = tmpdir(self) / "none" / "apt"
        sources, web = self.sources(**{"a.xz": b"alpha"})
        with self.assertRaises(self.b.FetchError) as raised:
            self.b.fetch(cache, sources=sources, offline=True, opener=web)
        self.assertIn("a.xz", str(raised.exception))
        self.assertFalse(cache.exists())

    def test_a_cache_folder_that_cannot_be_made_is_an_error_of_the_fetch(self):
        blocker = tmpdir(self) / "file"
        blocker.write_text("x")
        sources, web = self.sources(**{"a.xz": b"alpha"})
        with self.assertRaises(self.b.FetchError):
            self.b.fetch(blocker / "apt", sources=sources, opener=web)

    def test_a_file_that_moves_is_taken_only_when_asked_for(self):
        cache = tmpdir(self)
        moving = self.b.Source("popcon-by_inst.gz", "https://example.org/popcon", sha256(b"of the build"))
        web = FakeWeb({"https://example.org/popcon": b"of today"})
        with self.assertRaises(self.b.FetchError) as raised:
            self.b.fetch(cache, sources=(moving,), opener=web)
        self.assertIn("--accept-moved", str(raised.exception))
        self.assertEqual(list(cache.iterdir()), [])
        got = self.b.fetch(cache, sources=(moving,), opener=web, accept_moved=True)
        self.assertEqual(got[0].read_bytes(), b"of today")
        # another file is never taken with another hash
        other = self.b.Source("a.xz", "https://example.org/popcon", sha256(b"of the build"))
        with self.assertRaises(self.b.FetchError):
            self.b.fetch(tmpdir(self), sources=(other,), opener=web, accept_moved=True)

    def test_every_file_is_tried_and_the_errors_come_together(self):
        cache = tmpdir(self)
        sources, web = self.sources(**{"a.xz": b"alpha", "b.gz": b"beta", "c.gz": b"gamma"})
        web.pages["https://example.org/a.xz"] = b"changed"
        del web.pages["https://example.org/b.gz"]
        with self.assertRaises(self.b.FetchError) as raised:
            self.b.fetch(cache, sources=sources, opener=web)
        text = str(raised.exception)
        self.assertIn("a.xz", text)
        self.assertIn("b.gz", text)
        self.assertEqual((cache / "c.gz").read_bytes(), b"gamma")

    def test_a_cut_download_leaves_no_part_and_joins_the_errors(self):
        import http.client

        class Cut:
            def __init__(self, request, timeout=None):
                self.sent = False

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, size=-1):
                if self.sent:
                    raise http.client.IncompleteRead(b"", 10)
                self.sent = True
                return b"alp"

        cache = tmpdir(self)
        sources, web = self.sources(**{"a.xz": b"alpha", "b.gz": b"beta"})
        calls = []

        def opener(request, timeout=None):
            calls.append(request.full_url)
            return Cut(request) if request.full_url.endswith("a.xz") else web(request, timeout)
        with self.assertRaises(self.b.FetchError) as raised:
            self.b.fetch(cache, sources=sources, opener=opener)
        self.assertIn("a.xz", str(raised.exception))
        self.assertEqual(sorted(path.name for path in cache.iterdir()), ["b.gz"])    # no a.xz.part

    def test_a_network_error_names_the_file(self):
        cache = tmpdir(self)
        sources, _ = self.sources(**{"a.xz": b"alpha"})
        with self.assertRaises(self.b.FetchError) as raised:
            self.b.fetch(cache, sources=sources, opener=FakeWeb({}))
        self.assertIn("a.xz", str(raised.exception))
        self.assertIn("https://example.org/a.xz", str(raised.exception))

    def test_a_file_name_may_not_leave_the_cache(self):
        cache = tmpdir(self)
        source = self.b.Source("../evil.xz", "https://example.org/x", sha256(b"x"))
        with self.assertRaises(self.b.FetchError):
            self.b.fetch(cache, sources=(source,), opener=FakeWeb({"https://example.org/x": b"x"}))
        self.assertFalse((cache.parent / "evil.xz").exists())

    def test_the_recorded_inputs_are_named_when_they_are_missing(self):
        folder = tmpdir(self)
        with self.assertRaises(self.b.FetchError) as raised:
            self.b.check_recorded(folder)
        self.assertIn("deb-base-b.txt", str(raised.exception))
        self.assertIn("README", str(raised.exception))
        for name in self.b.recorded_names():
            (folder / name).write_text("x\n")
        self.b.check_recorded(folder)

    def test_the_options_choose_the_folder(self):
        parse = self.b.parse_args
        here = Path(TOOL).parent
        args = parse(["--fetch"], {"HOME": "/home/u"})
        self.assertEqual((args.downloads, args.fetch, args.offline), (Path("/home/u/.cache/tpot-cowrie/apt"), True, False))
        self.assertEqual(parse([], {"HOME": "/home/u"}).offline, True)
        explicit = parse(["/data/dl", "/data/out"], {"HOME": "/home/u"})
        self.assertEqual((explicit.downloads, explicit.out, explicit.verify), (Path("/data/dl"), Path("/data/out"), False))
        self.assertEqual(parse(["--cache", "/c"], {}).downloads, Path("/c"))
        self.assertEqual(parse(["--fetch", "/data/dl"], {}).downloads, Path("/data/dl"))
        self.assertTrue(parse(["--offline", "/data/dl"], {}).verify)
        moved = parse(["--fetch", "--accept-moved", "/data/dl", "/data/out"], {})
        self.assertEqual((moved.downloads, moved.out, moved.accept_moved), (Path("/data/dl"), Path("/data/out"), True))
        # the lists of a newer popcon never go into the checked-in folder by default
        for bad in (["--fetch", "--offline"], ["--accept-moved", "/data/dl"], ["a", "b", "c"], ["--accept-moved"],
                    ["--fetch", "--accept-moved"], ["--fetch", "--accept-moved", "/data/dl"]):
            with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                parse(bad, {"HOME": "/home/u"})
            self.assertEqual(raised.exception.code, 2)
        self.assertEqual(here.name, "tools")

    def test_the_readme_names_every_pinned_source(self):
        readme = (TOOL.parents[1] / "dist" / "tpot_personas" / "package_names" / "README.md").read_text()
        # the table of the sha256 of the inputs is SOURCES now; the README points at it and names the cache
        self.assertIn("SOURCES", readme)
        self.assertIn("~/.cache/tpot-cowrie/apt", readme)
        self.assertIn("--accept-moved", readme)
        # the limits of the rebuild: the recordings are gone, the lists of a rebuild are not the checked-in ones
        self.assertIn("recordings", readme)
        self.assertIn("apt_fallback", readme)
        self.assertIn("never overwrites", readme)
        for source in self.b.SOURCES:
            self.assertNotRegex(readme, rf"\| `{re.escape(source.name)}` \| `[0-9a-f]{{64}}` \|")


if __name__ == "__main__":
    unittest.main()
