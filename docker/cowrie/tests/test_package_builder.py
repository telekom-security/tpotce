"""The builder of the package lists (docker/cowrie/tools/build_package_names.py) on small indexes."""
import gzip
import importlib.util
import lzma
import unittest
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
                                "smtpdaemon > postfix", "system-release > redhat-release"])
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


if __name__ == "__main__":
    unittest.main()
