"""Portable migration package format and integrity checks."""

import base64
import hashlib
import importlib
import io
import json
import os
import platform
from pathlib import Path
import subprocess
import stat
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from tpotctl.envfile import EnvFile


try:
    migration = importlib.import_module("tpotctl.migration")
except ImportError:
    migration = None
try:
    migration_cli = importlib.import_module("tpotctl.migration_cli")
except ImportError:
    migration_cli = None
try:
    migration_import = importlib.import_module("tpotctl.migration_import")
except ImportError:
    migration_import = None


SOURCE = {
    "release": "24.04.1",
    "commit": "a" * 40,
    "role": "HIVE",
    "edition": "STANDARD",
    "os_id": "debian",
    "os_version": "12",
    "architecture": "amd64",
    "userland_bits": 64,
    "elasticsearch_version": "8.16.1",
    "kibana_version": "8.16.1",
    "image_digests": {"elasticsearch": "sha256:" + "b" * 64},
    "data_path": "/data/tpot",
}


def manifest_for(path: str, data: bytes) -> dict:
    return {
        "format": "tpot-migration",
        "schema": 1,
        "created_at": "2025-01-01T00:00:00Z",
        "source": SOURCE,
        "scope": ["configuration"],
        "files": [{
            "path": path,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }],
    }


def tar_member(name: str, data: bytes, kind=tarfile.REGTYPE) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = kind
    member.size = len(data) if kind == tarfile.REGTYPE else 0
    member.mode = 0o600
    member.uid = member.gid = 0
    return member


def write_archive(path: str, manifest: dict, members) -> None:
    with tarfile.open(path, "w") as archive:
        raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        archive.addfile(tar_member("manifest.json", raw), io.BytesIO(raw))
        for member, data in members:
            archive.addfile(member, io.BytesIO(data) if member.isfile() else None)


class MigrationPackageTest(unittest.TestCase):

    def setUp(self):
        self.assertIsNotNone(migration, "the migration package module is not implemented")
        self.temp = tempfile.TemporaryDirectory(prefix="tpot-migration-test-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        if hasattr(self, "temp"):
            self.temp.cleanup()

    def test_round_trip_is_private_and_validation_does_not_extract(self):
        source_dir = self.root / "source"
        source_dir.mkdir()
        env = source_dir / "env"
        cert = source_dir / "hive.crt"
        env.write_bytes(b"TPOT_TYPE=HIVE\n")
        cert.write_bytes(b"certificate\n")
        package = self.root / "migration.tar"

        manifest = migration.create_package(
            str(package), SOURCE, ["configuration", "identity"],
            {"configuration/.env": str(env), "identity/hive.crt": str(cert)},
        )

        self.assertEqual(migration.validate_package(str(package)), manifest)
        self.assertEqual(stat.S_IMODE(package.stat().st_mode), 0o600)
        self.assertEqual(manifest["format"], "tpot-migration")
        self.assertEqual(manifest["schema"], 1)
        records = {record["path"]: record for record in manifest["files"]}
        self.assertEqual(records["configuration/.env"]["size"], 15)
        self.assertEqual(records["configuration/.env"]["sha256"],
                         hashlib.sha256(b"TPOT_TYPE=HIVE\n").hexdigest())
        self.assertEqual(records["identity/hive.crt"]["sha256"], hashlib.sha256(b"certificate\n").hexdigest())
        self.assertFalse((self.root / "payload").exists())

    def test_rejects_unsafe_logical_paths_and_nonregular_sources(self):
        regular = self.root / "source"
        regular.write_bytes(b"private\n")
        for name in ("../outside", "/absolute", "identity\\secret"):
            with self.subTest(name=name), self.assertRaises(migration.MigrationError):
                migration.create_package(str(self.root / "unsafe.tar"), SOURCE, ["identity"], {name: str(regular)})
        with self.assertRaises(migration.MigrationError):
            migration.create_package(str(self.root / "wrong-scope.tar"), SOURCE, ["identity"],
                                     {"configuration/file": str(regular)})

        directory = self.root / "directory"
        directory.mkdir()
        with self.assertRaises(migration.MigrationError):
            migration.create_package(str(self.root / "directory.tar"), SOURCE, ["identity"],
                                     {"identity/directory": str(directory)})

        link = self.root / "link"
        link.symlink_to(regular)
        with self.assertRaises(migration.MigrationError):
            migration.create_package(str(self.root / "link.tar"), SOURCE, ["identity"],
                                     {"identity/link": str(link)})

    def test_refuses_to_replace_an_existing_package(self):
        source = self.root / "source"
        source.write_bytes(b"private\n")
        package = self.root / "existing.tar"
        package.write_bytes(b"keep this file")

        with self.assertRaises(migration.MigrationError):
            migration.create_package(str(package), SOURCE, ["configuration"],
                                     {"configuration/file": str(source)})

        self.assertEqual(package.read_bytes(), b"keep this file")

    def test_rejects_unlisted_source_metadata_without_creating_a_package(self):
        source_file = self.root / "source"
        source_file.write_bytes(b"private\n")
        destination = self.root / "migration.tar"
        source = dict(SOURCE, hostname="hive.example")

        with self.assertRaises(migration.MigrationError):
            migration.create_package(str(destination), source, ["configuration"],
                                     {"configuration/file": str(source_file)})

        self.assertFalse(destination.exists())

    def test_validation_rejects_traversal_links_duplicates_and_hash_mismatch(self):
        payload = b"expected"
        traversal = self.root / "traversal.tar"
        write_archive(
            str(traversal), manifest_for("../outside", payload),
            [(tar_member("payload/../outside", payload), payload)],
        )
        with self.assertRaises(migration.MigrationError):
            migration.validate_package(str(traversal))

        linked = self.root / "linked.tar"
        write_archive(
            str(linked), manifest_for("configuration/file", b""),
            [(tar_member("payload/configuration/file", b"", tarfile.SYMTYPE), b"")],
        )
        with self.assertRaises(migration.MigrationError):
            migration.validate_package(str(linked))

        duplicate = self.root / "duplicate.tar"
        write_archive(
            str(duplicate), manifest_for("configuration/file", payload),
            [
                (tar_member("payload/configuration/file", payload), payload),
                (tar_member("payload/configuration/file", payload), payload),
            ],
        )
        with self.assertRaises(migration.MigrationError):
            migration.validate_package(str(duplicate))

        unknown = self.root / "unknown.tar"
        write_archive(
            str(unknown), manifest_for("configuration/file", payload),
            [
                (tar_member("payload/configuration/file", payload), payload),
                (tar_member("unexpected", b"extra"), b"extra"),
            ],
        )
        with self.assertRaises(migration.MigrationError):
            migration.validate_package(str(unknown))

        mismatch = self.root / "mismatch.tar"
        write_archive(
            str(mismatch), manifest_for("configuration/file", payload),
            [(tar_member("payload/configuration/file", b"tampered"), b"tampered")],
        )
        with self.assertRaises(migration.MigrationError):
            migration.validate_package(str(mismatch))

    def test_validation_takes_the_pax_size_of_members_of_8_gib_and_more(self):
        # tarfile writes the size of a member of 8 GiB or more as a pax record (ustar has 11 octal digits)
        payload = b"a large payload"
        member = tar_member("payload/configuration/file", payload)
        member.pax_headers = {"size": str(len(payload))}
        large = self.root / "large.tar"
        write_archive(str(large), manifest_for("configuration/file", payload), [(member, payload)])
        os.chmod(large, 0o600)

        self.assertEqual(migration.validate_package(str(large))["files"][0]["path"], "configuration/file")

        for header in ({"uid": "1000"}, {"linkpath": "/etc/shadow"}, {"size": str(len(payload)), "mtime": "1.5"}):
            member = tar_member("payload/configuration/file", payload)
            member.pax_headers = header
            other = self.root / "other.tar"
            write_archive(str(other), manifest_for("configuration/file", payload), [(member, payload)])
            os.chmod(other, 0o600)
            with self.subTest(header=header), self.assertRaises(migration.MigrationError):
                migration.validate_package(str(other))
            other.unlink()


class SourceExportTest(unittest.TestCase):

    def setUp(self):
        self.assertIsNotNone(migration, "the migration package module is not implemented")
        self.assertIsNotNone(migration_cli, "the portable migration CLI is not implemented")
        self.temp = tempfile.TemporaryDirectory(prefix="tpot-migration-source-test-")
        self.root = Path(self.temp.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        self.data = self.repo / "data"
        self.data.mkdir()
        (self.repo / ".git").mkdir()
        (self.repo / "version").write_text("24.04.1\n", encoding="utf-8")
        (self.repo / ".env").write_text(
            "TPOT_TYPE=HIVE\nTPOT_DATA_PATH=./data\nTPOT_DOCKER_COMPOSE=./docker-compose.yml\n"
            "TPOT_VERSION=24.04.1\nWEB_USER=admin:super-secret-value\n",
            encoding="utf-8",
        )
        (self.repo / "docker-compose.yml").write_text("# T-Pot: STANDARD\nservices: {}\n", encoding="utf-8")
        self.os_release = self.root / "os-release"
        self.os_release.write_text('ID=debian\nVERSION_ID="12"\n', encoding="utf-8")
        self.output_dir = self.root / "private"
        self.output_dir.mkdir(mode=0o700)
        self.package = self.output_dir / "migration.tar"
        self.compose = {
            "services": {
                "tpotinit": {
                    "image": "example/tpotinit:24.04.1",
                    "volumes": [{"type": "bind", "source": str(self.data), "target": "/data"}],
                },
                "elasticsearch": {"image": "example/elasticsearch:24.04.1"},
                "kibana": {"image": "example/kibana:24.04.1"},
            },
        }
        self.identity_files = {
            "uuid": b"tpot-uuid\n",
            "sensors.json": b'{"version":1,"sensors":[]}\n',
            "nginx/conf/nginxpasswd": b"admin:$2y$hash\n",
            "nginx/conf/nginxpasswd.lost": b"legacy:$2y$lost\n",
            "nginx/conf/lswebpasswd": b"sensor:$2y$hash\n",
            "nginx/conf/lswebpasswd.lost": b"old-sensor:$2y$lost\n",
            "nginx/cert/nginx.crt": b"certificate\n",
            "nginx/cert/nginx.key": b"private-key\n",
            "hive.crt": b"hive-certificate\n",
            "cowrie/keys/ssh_host_rsa_key": b"host-key\n",
        }
        for relative, content in self.identity_files.items():
            path = self.data / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        ignored = self.data / "cowrie/log/session.json"
        ignored.parent.mkdir(parents=True)
        ignored.write_text('{"attack":"not identity"}\n', encoding="utf-8")

    def tearDown(self):
        if hasattr(self, "temp"):
            self.temp.cleanup()

    def _run(self, command, **_kwargs):
        if command[:3] == ["git", "-C", os.path.realpath(self.repo)]:
            return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
        if command[:2] == ["docker", "compose"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.compose), "")
        if command[:3] == ["docker", "image", "inspect"]:
            image = command[-1]
            if image == "missing":
                return subprocess.CompletedProcess(command, 1, "", "image not found")
            service = image.split("/", 1)[-1].split(":", 1)[0]
            version = {"elasticsearch": "ES_VER=8.16.1", "kibana": "KB_VER=8.16.1"}.get(service)
            config = {"Env": [version] if version else []}
            result = {
                "Id": "sha256:" + hashlib.sha256(image.encode("utf-8")).hexdigest(),
                "Config": config,
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")
        return subprocess.CompletedProcess(command, 1, "", "unexpected command")

    def _cli(self, args, *, machine="x86_64"):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), \
                mock.patch("tpotctl.migration.subprocess.run", side_effect=self._run), \
                mock.patch("tpotctl.migration.OS_RELEASE_PATH", str(self.os_release)), \
                mock.patch("tpotctl.migration.platform.machine", return_value=machine):
            code = migration_cli.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_export_contains_only_curated_private_files_and_never_prints_secrets(self):
        code, stdout, stderr = self._cli(["export", "--source", str(self.repo), "--output", str(self.package)])

        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        self.assertNotIn("super-secret-value", stdout)
        self.assertNotIn("private-key", stdout)
        self.assertEqual(stat.S_IMODE(self.package.stat().st_mode), 0o600)
        manifest = migration.validate_package(str(self.package))
        self.assertEqual(manifest["scope"], ["configuration", "identity"])
        self.assertEqual(manifest["source"]["release"], "24.04.1")
        self.assertEqual(manifest["source"]["role"], "HIVE")
        self.assertEqual(manifest["source"]["elasticsearch_version"], "8.16.1")
        self.assertEqual(manifest["source"]["kibana_version"], "8.16.1")
        self.assertEqual(manifest["source"]["architecture"], "amd64")
        self.assertEqual(manifest["source"]["data_path"], os.path.realpath(self.data))
        self.assertEqual(
            {item["path"] for item in manifest["files"]},
            {"configuration/.env", "configuration/compose.yml"}
            | {f"identity/{path}" for path in self.identity_files},
        )
        with tarfile.open(self.package, "r:") as archive:
            env = archive.extractfile("payload/configuration/.env").read()
            self.assertIn(b"super-secret-value", env)
            self.assertNotIn("payload/identity/cowrie/log/session.json", archive.getnames())

    def test_validate_reports_metadata_without_payload_values(self):
        code, _stdout, stderr = self._cli(["export", "--source", str(self.repo), "--output", str(self.package)])
        self.assertEqual(code, 0, stderr)

        code, stdout, stderr = self._cli(["validate", str(self.package)])

        self.assertEqual(code, 0, stderr)
        self.assertIn("24.04.1", stdout)
        self.assertIn("HIVE", stdout)
        self.assertNotIn("super-secret-value", stdout)
        self.assertNotIn("private-key", stdout)

    def test_inspect_reads_local_image_versions_without_showing_digest_or_env(self):
        code, stdout, stderr = self._cli(["inspect", "--source", str(self.repo)])

        self.assertEqual(code, 0, stderr)
        self.assertIn("8.16.1", stdout)
        self.assertIn("HIVE", stdout)
        self.assertNotIn("super-secret-value", stdout)
        self.assertNotIn("sha256:", stdout)

    def test_inspect_accepts_mobile_without_elasticsearch_or_kibana(self):
        (self.repo / ".env").write_text(
            "TPOT_TYPE=MOBILE\nTPOT_DATA_PATH=./data\nTPOT_DOCKER_COMPOSE=./docker-compose.yml\n"
            "TPOT_VERSION=24.04.1\n",
            encoding="utf-8",
        )
        (self.repo / "docker-compose.yml").write_text("# T-Pot: MOBILE\nservices: {}\n", encoding="utf-8")
        self.compose = {
            "services": {
                "tpotinit": {
                    "image": "example/tpotinit:24.04.1",
                    "volumes": [{"type": "bind", "source": str(self.data), "target": "/data"}],
                },
            },
        }

        code, _stdout, stderr = self._cli(
            ["export", "--source", str(self.repo), "--output", str(self.package)])

        self.assertEqual(code, 0, stderr)
        manifest = migration.validate_package(str(self.package))
        self.assertEqual(manifest["source"]["role"], "MOBILE")
        self.assertIsNone(manifest["source"]["elasticsearch_version"])
        self.assertIsNone(manifest["source"]["kibana_version"])

        code, stdout, stderr = self._cli(["inspect", "--source", str(self.repo)])

        self.assertEqual(code, 0, stderr)
        self.assertIn("MOBILE", stdout)
        self.assertIn("Elasticsearch not present", stdout)
        self.assertIn("Kibana not present", stdout)

    def _mobile_compose(self):
        self.compose = {
            "services": {
                "tpotinit": {
                    "image": "example/tpotinit:24.04.1",
                    "volumes": [{"type": "bind", "source": str(self.data), "target": "/data"}],
                },
            },
        }

    def test_export_takes_the_mobile_role_from_the_edition_of_a_24_04_1_mobile(self):
        # install.sh of 24.04.1 keeps TPOT_TYPE=HIVE and an empty WEB_USER for T-Pot Mobile
        (self.repo / ".env").write_text(
            "TPOT_TYPE=HIVE\nWEB_USER=\nTPOT_DATA_PATH=./data\nTPOT_DOCKER_COMPOSE=./docker-compose.yml\n"
            "TPOT_VERSION=24.04.1\n",
            encoding="utf-8",
        )
        (self.repo / "docker-compose.yml").write_text("# T-Pot: MOBILE\nservices: {}\n", encoding="utf-8")
        self._mobile_compose()

        code, _stdout, stderr = self._cli(
            ["export", "--source", str(self.repo), "--output", str(self.package)])

        self.assertEqual(code, 0, stderr)
        manifest = migration.validate_package(str(self.package))
        self.assertEqual(manifest["source"]["role"], "MOBILE")
        self.assertEqual(manifest["source"]["edition"], "MOBILE")

    def test_export_refuses_a_role_that_does_not_fit_the_edition(self):
        for tpot_type, header in (("SENSOR", "MOBILE"), ("MOBILE", "STANDARD")):
            with self.subTest(tpot_type=tpot_type, edition=header):
                (self.repo / ".env").write_text(
                    f"TPOT_TYPE={tpot_type}\nTPOT_DATA_PATH=./data\nTPOT_DOCKER_COMPOSE=./docker-compose.yml\n"
                    "TPOT_VERSION=24.04.1\n",
                    encoding="utf-8",
                )
                (self.repo / "docker-compose.yml").write_text(f"# T-Pot: {header}\nservices: {{}}\n",
                                                              encoding="utf-8")
                self._mobile_compose()
                if self.package.exists():
                    self.package.unlink()

                code, _stdout, _stderr = self._cli(
                    ["export", "--source", str(self.repo), "--output", str(self.package)])

                self.assertNotEqual(code, 0)
                self.assertFalse(self.package.exists())

    def test_export_rejects_unsupported_release_architecture_and_identity_links(self):
        (self.repo / "version").write_text("25.04.0\n", encoding="utf-8")
        code, _stdout, _stderr = self._cli(["export", "--source", str(self.repo), "--output", str(self.package)])
        self.assertEqual(code, 1)
        self.assertFalse(self.package.exists())

        (self.repo / "version").write_text("24.04.1\n", encoding="utf-8")
        code, _stdout, _stderr = self._cli(
            ["export", "--source", str(self.repo), "--output", str(self.package)], machine="armv7l")
        self.assertEqual(code, 1)
        self.assertFalse(self.package.exists())

        link = self.data / "cowrie/keys/linked-key"
        link.symlink_to(self.data / "cowrie/keys/ssh_host_rsa_key")
        code, _stdout, _stderr = self._cli(["export", "--source", str(self.repo), "--output", str(self.package)])
        self.assertEqual(code, 1)
        self.assertFalse(self.package.exists())

    def test_export_rejects_a_checkout_whose_env_names_another_release(self):
        """An update that stopped halfway leaves the checkout of the new release with the .env of the old one."""
        env = (self.repo / ".env").read_text(encoding="utf-8")
        for name, text in (("missing", env.replace("TPOT_VERSION=24.04.1\n", "")),
                           ("other", env.replace("TPOT_VERSION=24.04.1", "TPOT_VERSION=24.04.2"))):
            with self.subTest(name):
                self.package.unlink(missing_ok=True)
                (self.repo / ".env").write_text(text, encoding="utf-8")
                code, stdout, stderr = self._cli(
                    ["export", "--source", str(self.repo), "--output", str(self.package)])
                self.assertEqual(code, 1, stdout + stderr)
                self.assertIn("TPOT_VERSION", stdout + stderr)
                self.assertFalse(self.package.exists())

    def test_export_rejects_uninspectable_compose_or_missing_required_image(self):
        self.compose = {"services": {"elasticsearch": {"image": "example/elasticsearch:24.04.1"}}}
        code, _stdout, _stderr = self._cli(["export", "--source", str(self.repo), "--output", str(self.package)])
        self.assertEqual(code, 1)
        self.assertFalse(self.package.exists())

        self.compose = {"services": {"tpotinit": {"image": "missing"}}}
        code, _stdout, _stderr = self._cli(["export", "--source", str(self.repo), "--output", str(self.package)])
        self.assertEqual(code, 1)
        self.assertFalse(self.package.exists())

    def test_export_rejects_symlinked_identity_parent_directories(self):
        cowrie = self.data / "cowrie"
        target = self.data / "cowrie-real"
        cowrie.rename(target)
        cowrie.symlink_to(target, target_is_directory=True)

        code, _stdout, _stderr = self._cli(["export", "--source", str(self.repo), "--output", str(self.package)])

        self.assertEqual(code, 1)
        self.assertFalse(self.package.exists())


class ImportFixture:
    """A fresh 24.04.2 target checkout and a staged source package, shared by the import tests."""

    def setUp(self):
        self.assertIsNotNone(migration, "the migration package module is not implemented")
        self.assertIsNotNone(migration_cli, "the portable migration CLI is not implemented")
        self.assertIsNotNone(migration_import, "the migration import module is not implemented")
        self.temp = tempfile.TemporaryDirectory(prefix="tpot-migration-import-test-")
        self.root = Path(self.temp.name)
        self.target = self.root / "target"
        self.target.mkdir()
        (self.target / ".git").mkdir()
        (self.target / "compose").mkdir()
        (self.target / "tpotctl").mkdir()
        (self.target / "compose" / "mobile.yml").write_text(
            "# T-Pot: MOBILE\nservices:\n  tpotinit: {}\n", encoding="utf-8")
        (self.target / "version").write_text("24.04.2\n", encoding="utf-8")
        (self.target / "env.example").write_text(
            "TPOT_TYPE=HIVE\nTPOT_DATA_PATH=./data\nTPOT_VERSION=24.04.2\n"
            "TPOT_REPO=ghcr.io/telekom-security\nTPOT_DOCKER_COMPOSE=./docker-compose.yml\n",
            encoding="utf-8",
        )
        self.source_dir = self.root / "source-files"
        self.source_dir.mkdir()
        self.source_env = self.source_dir / "env"
        self.source_env.write_text(
            "TPOT_TYPE=HIVE\nTPOT_DATA_PATH=/source/tpot-data\nTPOT_VERSION=24.04.1\n"
            "TPOT_REPO=source-registry\nWEB_USER="
            + base64.b64encode(b"admin:$2y$05$testhash\n").decode("ascii") + "\n",
            encoding="utf-8",
        )
        self.source_compose = self.source_dir / "compose.yml"
        self.source_compose.write_text(
            "# T-Pot: STANDARD\nservices:\n  tpotinit:\n    image: example/tpotinit\n  nginx: {}\n",
            encoding="utf-8",
        )
        self.identity = self.source_dir / "uuid"
        self.identity.write_bytes(b"source-uuid\n")
        self.package = self.root / "migration.tar"
        self.make_package()
        self.state = self.root / "state"

    def tearDown(self):
        if hasattr(self, "temp"):
            self.temp.cleanup()

    def make_package(self, source=None, scope=("configuration", "identity")):
        if self.package.exists():
            self.package.unlink()
        source_info = dict(SOURCE if source is None else source)
        if source is None:
            source_info["architecture"] = "arm64" if platform.machine().lower() in ("aarch64", "arm64") else "amd64"
        migration.create_package(
            str(self.package), source_info, scope,
            {
                "configuration/.env": str(self.source_env),
                "configuration/compose.yml": str(self.source_compose),
                "identity/uuid": str(self.identity),
            },
        )

    def stage(self, **overrides):
        release = {"NAME": "Debian GNU/Linux", "ID": "debian", "VERSION_ID": "13"}
        release.update(overrides.pop("release", {}))
        with mock.patch.object(migration_import.installer, "os_release", return_value=release), \
                mock.patch.object(migration_import.installer, "is_raspberry_pi_os",
                                  return_value=overrides.pop("raspberry_pi_os", False)), \
                mock.patch.object(migration_import.installer, "installed",
                                  return_value=overrides.pop("installed", False)):
            return migration_import.stage_import(
                str(self.package), str(self.target), str(self.state), **overrides)

    ADOPTED = "  nginx: {}\n"

    def prepare(self):
        (self.target / "compose" / "customizer.py").write_text("", encoding="utf-8")

        def customizer(command, **_kwargs):
            output = Path(command[command.index("-o") + 1])
            output.write_text(
                "# T-Pot: CUSTOM\n# customizer: version=2 base=STANDARD\n"
                "# customizer: add=\n# customizer: remove=\n# customizer: port=\n"
                "services:\n  tpotinit:\n    image: target/tpotinit\n" + self.ADOPTED,
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(migration_import.installer, "os_release",
                               return_value={"NAME": "Debian GNU/Linux", "ID": "debian", "VERSION_ID": "13"}), \
                mock.patch.object(migration_import.installer, "is_raspberry_pi_os", return_value=False), \
                mock.patch.object(migration_import.installer, "installed", return_value=False):
            return migration_import.prepare_import(str(self.target), str(self.state), customizer)


class MigrationImportTest(ImportFixture, unittest.TestCase):

    def test_hive_without_nginx_needs_no_web_user_and_stays_hive(self):
        self.ADOPTED = ""
        self.source_env.write_text("TPOT_TYPE=HIVE\nWEB_USER=\n")
        self.make_package()
        self.stage()
        self.prepare()
        migration_import.apply_import(str(self.target), str(self.state))
        self.assertEqual(EnvFile(str(self.target / ".env")).get("TPOT_TYPE"), "HIVE")

    def test_stage_import_is_private_and_does_not_change_target_configuration(self):
        result = self.stage()

        self.assertEqual(result["source_role"], "HIVE")
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.state / "package.tar").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.state / "pending.json").stat().st_mode), 0o600)
        self.assertNotIn("admin:", (self.state / "pending.json").read_text(encoding="utf-8"))
        self.assertFalse((self.target / ".env").exists())
        self.assertFalse((self.target / "docker-compose.yml").exists())
        self.assertFalse((self.target / "data").exists())

    def test_stage_import_rejects_incomplete_history_and_architecture_mismatch_without_staging(self):
        history = dict(SOURCE)
        self.make_package(history, ["configuration", "identity", "history"])
        with self.assertRaises(migration.MigrationError):
            self.stage()
        self.assertFalse(self.state.exists())

        self.package.unlink()
        architecture = "arm64" if platform.machine().lower() in ("x86_64", "amd64") else "amd64"
        arm_source = dict(SOURCE, architecture=architecture)
        self.make_package(arm_source)
        with self.assertRaises(migration.MigrationError):
            self.stage()
        self.assertFalse(self.state.exists())

    def test_stage_import_rejects_unsupported_host_and_existing_tpot_data(self):
        with self.assertRaises(migration.MigrationError):
            self.stage(release={"NAME": "Ubuntu", "ID": "ubuntu"})
        self.assertFalse(self.state.exists())

        data = self.target / "data"
        data.mkdir()
        (data / "existing").write_text("keep\n", encoding="utf-8")
        with self.assertRaises(migration.MigrationError):
            self.stage()
        self.assertFalse(self.state.exists())
        self.assertEqual((data / "existing").read_text(encoding="utf-8"), "keep\n")

    def test_stage_import_rejects_existing_configuration_or_installed_tpot(self):
        for path in (".env", "docker-compose.yml"):
            with self.subTest(path=path):
                (self.target / path).write_text("existing\n", encoding="utf-8")
                with self.assertRaises(migration.MigrationError):
                    self.stage()
                self.assertFalse(self.state.exists())
                (self.target / path).unlink()

        with self.assertRaises(migration.MigrationError):
            self.stage(installed=True)
        self.assertFalse(self.state.exists())

    def test_raspberry_pi_os_accepts_only_mobile_source_role(self):
        with self.assertRaises(migration.MigrationError):
            self.stage(raspberry_pi_os=True)
        self.assertFalse(self.state.exists())

        self.package.unlink()
        mobile = dict(SOURCE, role="MOBILE", edition="MOBILE", architecture="arm64")
        self.make_package(mobile)
        with mock.patch("tpotctl.migration_import.platform.machine", return_value="aarch64"):
            result = self.stage(raspberry_pi_os=True)
        self.assertEqual(result["source_role"], "MOBILE")
        self.assertTrue((self.state / "pending.json").is_file())

    def test_prepare_normalizes_legacy_mobile_before_start(self):
        # the configuration of a real T-Pot Mobile: TPOT_TYPE=HIVE, no web user, the MOBILE compose file
        self.source_env.write_text(
            "TPOT_TYPE=HIVE\nTPOT_DATA_PATH=/source/tpot-data\nTPOT_VERSION=24.04.1\n"
            "TPOT_REPO=source-registry\nWEB_USER=\n",
            encoding="utf-8",
        )
        self.source_compose.write_text("# T-Pot: MOBILE\nservices:\n  tpotinit: {}\n  elasticsearch: {}\n",
                                       encoding="utf-8")
        self.make_package(dict(SOURCE, role="MOBILE", edition="MOBILE", architecture="arm64"))

        def customizer(command, **_kwargs):
            raise AssertionError("MOBILE takes the built-in compose file, the customizer is not asked")

        with mock.patch("tpotctl.migration_import.platform.machine", return_value="aarch64"), \
                mock.patch.object(migration_import.installer, "os_release",
                                  return_value={"NAME": "Debian GNU/Linux", "ID": "debian", "VERSION_ID": "13"}), \
                mock.patch.object(migration_import.installer, "is_raspberry_pi_os", return_value=True), \
                mock.patch.object(migration_import.installer, "installed", return_value=False):
            migration_import.stage_import(str(self.package), str(self.target), str(self.state))
            report = migration_import.prepare_import(str(self.target), str(self.state), customizer)
            migration_import.apply_import(str(self.target), str(self.state))
            migration_import.apply_import(str(self.target), str(self.state))

        self.assertEqual(report["source_role"], "MOBILE")
        values = EnvFile(str(self.target / ".env")).values()
        self.assertEqual(values["TPOT_TYPE"], "MOBILE")
        self.assertEqual((self.target / "data/uuid").read_bytes(), self.identity.read_bytes())
        self.assertFalse(values.get("WEB_USER"))
        self.assertEqual((self.target / "docker-compose.yml").read_text(encoding="utf-8"),
                         "# T-Pot: MOBILE\nservices:\n  tpotinit: {}\n")

    def test_prepare_apply_and_finish_import_preserve_target_owned_settings(self):
        self.stage()
        (self.target / "compose" / "customizer.py").write_text("", encoding="utf-8")

        def customizer(command, **_kwargs):
            output = Path(command[command.index("-o") + 1])
            output.write_text(
                "# T-Pot: CUSTOM\n# customizer: version=2 base=STANDARD\n"
                "# customizer: add=\n# customizer: remove=\n# customizer: port=\n"
                "services:\n  tpotinit:\n    image: target/tpotinit\n  nginx: {}\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "new: target-service\n", "")

        with mock.patch.object(migration_import.installer, "os_release",
                               return_value={"NAME": "Debian GNU/Linux", "ID": "debian", "VERSION_ID": "13"}), \
                mock.patch.object(migration_import.installer, "is_raspberry_pi_os", return_value=False), \
                mock.patch.object(migration_import.installer, "installed", return_value=False):
            report = migration_import.prepare_import(str(self.target), str(self.state), customizer)

        self.assertIn("new: target-service", report["compose_report"])
        self.assertFalse((self.target / ".env").exists())
        self.assertFalse((self.target / "docker-compose.yml").exists())
        migration_import.apply_import(str(self.target), str(self.state))
        values = EnvFile(str(self.target / ".env")).values()
        self.assertEqual(values["TPOT_TYPE"], "HIVE")
        self.assertEqual(values["TPOT_DATA_PATH"], "./data")
        self.assertEqual(values["TPOT_VERSION"], "24.04.2")
        self.assertEqual(values["TPOT_REPO"], "ghcr.io/telekom-security")
        self.assertEqual(values["WEB_USER"], base64.b64encode(b"admin:$2y$05$testhash\n").decode("ascii"))
        self.assertEqual((self.target / "docker-compose.yml").read_text(encoding="utf-8"),
                         "# T-Pot: CUSTOM\n# customizer: version=2 base=STANDARD\n"
                         "# customizer: add=\n# customizer: remove=\n# customizer: port=\n"
                         "services:\n  tpotinit:\n    image: target/tpotinit\n  nginx: {}\n")
        self.assertEqual((self.target / "data" / "uuid").read_bytes(), b"source-uuid\n")
        installed_env = EnvFile(str(self.target / ".env"))
        installed_env.set("TPOT_AUTO_UPDATES", "DISABLED")
        (self.target / ".env").write_text(installed_env.text(), encoding="utf-8")
        with mock.patch.object(migration_import.installer, "os_release",
                               return_value={"NAME": "Debian GNU/Linux", "ID": "debian", "VERSION_ID": "13"}), \
                mock.patch.object(migration_import.installer, "is_raspberry_pi_os", return_value=False), \
                mock.patch.object(migration_import.installer, "installed", return_value=False):
            self.assertEqual(migration_import.pending_source(str(self.target), str(self.state))["role"], "HIVE")
        migration_import.apply_import(str(self.target), str(self.state))

        migration_import.finish_import(str(self.state))

        self.assertFalse((self.state / "package.tar").exists())
        self.assertFalse((self.state / "pending.json").exists())
        self.assertFalse((self.state / "prepared").exists())

    def test_pending_import_allows_retry_after_tpot_service_is_installed(self):
        self.stage()
        (self.target / "compose" / "customizer.py").write_text("", encoding="utf-8")

        def customizer(command, **_kwargs):
            output = Path(command[command.index("-o") + 1])
            output.write_text(
                "# T-Pot: CUSTOM\n# customizer: version=2 base=STANDARD\n"
                "# customizer: add=\n# customizer: remove=\n# customizer: port=\n"
                "services:\n  tpotinit:\n    image: target/tpotinit\n  nginx: {}\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        service_file = self.root / "tpot.service"
        with mock.patch.object(migration_import.installer, "SERVICE_FILE", str(service_file)), \
                mock.patch.object(migration_import.installer, "os_release",
                                  return_value={"NAME": "Debian GNU/Linux", "ID": "debian",
                                                "VERSION_ID": "13"}), \
                mock.patch.object(migration_import.installer, "is_raspberry_pi_os", return_value=False):
            migration_import.prepare_import(str(self.target), str(self.state), customizer)
            migration_import.apply_import(str(self.target), str(self.state))
            service_file.write_text("installed by the failed attempt\n", encoding="utf-8")

            self.assertEqual(migration_import.pending_source(str(self.target), str(self.state))["role"], "HIVE")

    def test_prepare_rejects_a_hive_without_a_valid_web_user(self):
        source = self.source_env.read_text(encoding="utf-8").replace(
            "WEB_USER=" + base64.b64encode(b"admin:$2y$05$testhash\n").decode("ascii") + "\n",
            "WEB_USER=\n",
        )
        self.source_env.write_text(source, encoding="utf-8")
        self.make_package()
        self.stage()
        (self.target / "compose" / "customizer.py").write_text("", encoding="utf-8")

        def customizer(command, **_kwargs):
            output = Path(command[command.index("-o") + 1])
            output.write_text(
                "# T-Pot: CUSTOM\n# customizer: version=2 base=STANDARD\n"
                "# customizer: add=\n# customizer: remove=\n# customizer: port=\n"
                "services:\n  tpotinit: {}\n  nginx: {}\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(migration_import.installer, "os_release",
                               return_value={"NAME": "Debian GNU/Linux", "ID": "debian", "VERSION_ID": "13"}), \
                mock.patch.object(migration_import.installer, "is_raspberry_pi_os", return_value=False), \
                mock.patch.object(migration_import.installer, "installed", return_value=False), \
                self.assertRaises(migration.MigrationError):
            migration_import.prepare_import(str(self.target), str(self.state), customizer)
        self.assertFalse((self.target / ".env").exists())

    def test_import_command_requires_yes_when_input_is_not_interactive(self):
        with mock.patch.object(migration_import, "stage_import") as stage:
            result = migration_cli.main(["import", str(self.package)])

        self.assertEqual(result, 2)
        stage.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_stage_import_refuses_a_data_folder_others_can_write_before_it_stages(self):
        # the restore claims the new data folder only in a folder of this user that others cannot change
        self.target.chmod(0o775)
        with self.assertRaisesRegex(migration.MigrationError, "others cannot write"):
            self.stage()
        self.assertFalse(self.state.exists())

    def test_an_interrupted_import_does_not_point_to_the_source_recovery(self):
        output = io.StringIO()
        with mock.patch.object(migration_cli, "stage_import", side_effect=KeyboardInterrupt), \
                redirect_stdout(output), redirect_stderr(output):
            result = migration_cli.main(["import", str(self.package), "--yes"])
        self.assertEqual(result, 130)
        self.assertIn("cancelled", output.getvalue())
        self.assertNotIn("resume-source", output.getvalue())

    def test_import_yes_stages_the_package_without_printing_credentials(self):
        output = io.StringIO()
        with mock.patch.object(migration_cli, "stage_import",
                               return_value={"source_role": "HIVE", "source_release": "24.04.1"}):
            with redirect_stdout(output):
                result = migration_cli.main(["import", str(self.package), "--yes"])

        self.assertEqual(result, 0)
        self.assertIn("staged for HIVE", output.getvalue())
        self.assertNotIn("admin:$2y$", output.getvalue())


if __name__ == "__main__":
    unittest.main()
