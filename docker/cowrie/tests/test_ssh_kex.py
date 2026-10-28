"""The key exchanges and the server-sig-algs of the patched Cowrie: [ssh] kex_algorithms orders and
narrows what Twisted offers (ssh/factory.py), [ssh] server_sig_algs is the value of the EXT_INFO the
transport sends, "" for a server that sends none (ssh/transport.py). A persona without an SSH server
([ssh] drop_connections) gets each connection closed before the version string."""
import ast
import shutil
import struct
import subprocess
import sys
import textwrap
import time
import types
import unittest
from pathlib import Path

from tests.helpers import DIST, tmpdir
from tests.test_patch import FakeConfig, module_all
from tpot_personas import catalog, ssh_profiles
from tpot_personas.identity import draw
from tpot_personas.render import render_cfg

FIXTURE = Path(__file__).parent / "fixtures" / "v3.1.3"
PATCH = DIST / "patch_cowrie_persona_support.py"
# Twisted 26.4: _kex.getSupportedKeyExchanges() (by preference)
TWISTED_ORDER = [b"curve25519-sha256", b"curve25519-sha256@libssh.org", b"ecdh-sha2-nistp256", b"ecdh-sha2-nistp384",
                 b"ecdh-sha2-nistp521", b"diffie-hellman-group-exchange-sha256",
                 b"diffie-hellman-group-exchange-sha1", b"diffie-hellman-group14-sha1"]


class SshKexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        handle = tempfile.TemporaryDirectory()
        cls.addClassCleanup(handle.cleanup)
        root = Path(handle.name) / "cowrie"
        shutil.copytree(FIXTURE, root)
        subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)], check=True, capture_output=True,
                       text=True)
        cls.factory = (root / "src/cowrie/ssh/factory.py").read_text()
        # the excerpt has the methods of the class only: the class line makes it a module
        cls.transport = "class HoneyPotSSHTransport:\n" + (root / "src/cowrie/ssh/transport.py").read_text()

    def key_exchanges(self, value, supported=TWISTED_ORDER):
        config = FakeConfig({} if value is None else {("ssh", "kex_algorithms"): value})

        class Factory:
            class SSHFactory:
                pass

        namespace = module_all(self.factory, {"CowrieConfig": config, "transport": object(), "factory": Factory,
                                              "Logger": lambda: None})
        return namespace["tpot_key_exchanges"](list(supported))

    def test_factory_offers_the_key_exchanges_of_the_persona(self):
        self.assertIn("        t.supportedKeyExchanges = tpot_key_exchanges(t.supportedKeyExchanges)\n"
                      "\n        if not self.primes:\n", self.factory)
        self.assertEqual(self.key_exchanges("curve25519-sha256@libssh.org,diffie-hellman-group14-sha1"),
                         [b"curve25519-sha256@libssh.org", b"diffie-hellman-group14-sha1"])
        # the order of the persona, not Twisted's
        self.assertEqual(self.key_exchanges("ecdh-sha2-nistp521, curve25519-sha256"),
                         [b"ecdh-sha2-nistp521", b"curve25519-sha256"])

    def test_factory_leaves_out_what_twisted_has_not(self):
        self.assertEqual(self.key_exchanges("sntrup761x25519-sha512@openssh.com,curve25519-sha256,curve25519-sha256"),
                         [b"curve25519-sha256"])
        # a Twisted without curve25519 (no X25519 in its cryptography): what is left
        self.assertEqual(self.key_exchanges("curve25519-sha256,ecdh-sha2-nistp256", TWISTED_ORDER[2:]),
                         [b"ecdh-sha2-nistp256"])
        # unset or nothing usable: Twisted's own list, as upstream
        self.assertEqual(self.key_exchanges(None), TWISTED_ORDER)
        self.assertEqual(self.key_exchanges("mlkem768x25519-sha256"), TWISTED_ORDER)

    def send_ext_info(self, value, peer=True):
        """sendExtInfo of the patched HoneyPotSSHTransport with [ssh] server_sig_algs = value (None:
        unset); returns the extensions Twisted's sendExtInfo got (None: not called)."""
        start = self.transport.index("    def sendExtInfo(self, extensions")
        end = self.transport.index("\n    def ", start + 10)
        body = textwrap.dedent(self.transport[start:end])
        sent = []

        class Base:
            @staticmethod
            def sendExtInfo(transport_self, extensions):
                sent.append(list(extensions))

        class TransportModule:
            SSHServerTransport = Base

        config = FakeConfig({} if value is None else {("ssh", "server_sig_algs"): value})
        namespace = {"CowrieConfig": config, "transport": TransportModule}
        exec(compile(ast.parse(body), "<patched transport>", "exec"), namespace)  # noqa: S102
        namespace["sendExtInfo"](object(), [(b"server-sig-algs", b"rsa-sha2-512,rsa-sha2-256,ssh-ed25519")])
        return sent[0] if sent else None

    def send_packets(self):
        """The sendPacket methods of the class HoneyPotSSHTransport of the patched ssh/transport.py."""
        tree = ast.parse(self.transport)
        return [node for cls in tree.body if isinstance(cls, ast.ClassDef) and cls.name == "HoneyPotSSHTransport"
                for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "sendPacket"]

    def send_packet(self, value, message_type, payload, closed=False):
        """Cowrie's own sendPacket of the patched HoneyPotSSHTransport (the one method of that name in the class)
        with [ssh] ext_info_s = value (None: unset), on a transport with no encryption; returns (the payload
        that was written, its padding, ourKexInitPayload, the written bytes)."""
        methods = self.send_packets()
        self.assertEqual(len(methods), 1)
        written = []

        class Ciphers:
            encBlockSize = 8

            @staticmethod
            def encrypt(packet):
                return packet

            @staticmethod
            def makeMAC(sequence, packet):
                return b""

        class TransportModule:
            MSG_KEXINIT = 20

        namespace = {"CowrieConfig": FakeConfig({} if value is None else {("ssh", "ext_info_s"): value}),
                     "transport": TransportModule, "struct": struct,
                     "randbytes": types.SimpleNamespace(secureRandom=lambda n: b"\x55" * n), "Any": object}
        exec(compile(ast.Module([methods[0]], []), "<patched transport>", "exec"), namespace)  # noqa: S102
        owner = types.SimpleNamespace(
            transport=None if closed else types.SimpleNamespace(write=written.append), _keyExchangeState=0,
            _KEY_EXCHANGE_NONE=0, outgoingCompression=None, currentEncryptions=Ciphers, outgoingPacketSequence=0,
            ourKexInitPayload=bytes((message_type,)) + payload)
        namespace["sendPacket"](owner, message_type, payload)
        if closed:
            self.assertEqual(written, [])
            return None, None, owner.ourKexInitPayload, b""
        wire = b"".join(written)
        total, pad = struct.unpack("!LB", wire[:5])
        self.assertEqual(len(wire), total + 4)
        self.assertEqual(wire[5] , message_type)
        return wire[6:total + 4 - pad], wire[total + 4 - pad:], owner.ourKexInitPayload, wire

    def kexinit(self, kex=(b"curve25519-sha256", b"ecdh-sha2-nistp256", b"ext-info-s")):
        def ns(data):
            return len(data).to_bytes(4, "big") + data

        lists = [b",".join(kex)] + [b"x", b"c", b"c", b"m", b"m", b"none", b"none", b"", b""]
        return bytes(range(16)) + b"".join(ns(item) for item in lists) + b"\0\0\0\0\0", lists

    def test_a_server_profile_without_ext_info_sends_no_ext_info_s(self):
        payload, lists = self.kexinit()
        sent, padding, kept, _ = self.send_packet("false", 20, payload)

        def ns(data):
            return len(data).to_bytes(4, "big") + data
        lists = [b"curve25519-sha256,ecdh-sha2-nistp256"] + lists[1:]
        expected = bytes(range(16)) + b"".join(ns(item) for item in lists) + b"\0\0\0\0\0"
        self.assertEqual(sent, expected)
        # the exchange hash uses what was sent, and Cowrie still pads a KEXINIT with 0
        self.assertEqual(kept, bytes((20,)) + expected)
        self.assertEqual(set(padding), {0})
        # the only entry of the list: nothing is left to send
        sent, _, kept, _ = self.send_packet("no", 20, self.kexinit(kex=(b"ext-info-s",))[0])
        self.assertEqual(sent[16:20], (0).to_bytes(4, "big"))
        self.assertEqual(kept, bytes((20,)) + sent)

    def test_a_server_profile_with_ext_info_keeps_ext_info_s(self):
        payload, _ = self.kexinit()
        for value in ("true", "yes", None):
            with self.subTest(value=value):
                sent, padding, kept, _ = self.send_packet(value, 20, payload)
                self.assertEqual(sent, payload)
                self.assertEqual(kept, bytes((20,)) + payload)
                self.assertIn(b"ext-info-s", sent)
                self.assertEqual(set(padding), {0})

    def test_the_cut_is_in_the_one_send_packet_of_the_class(self):
        # a second sendPacket in the class would shadow the first: Cowrie's own is the one that is patched
        found = self.send_packets()
        self.assertEqual(len(found), 1)
        text = ast.get_source_segment(self.transport, found[0])
        self.assertIn('CowrieConfig.getboolean("ssh", "ext_info_s"', text)
        # Cowrie's guard for a closed connection stays first, the padding after
        self.assertLess(text.index("self.transport is None"), text.index("ext_info_s"))
        self.assertLess(text.index("ext_info_s"), text.index("randbytes.secureRandom"))
        self.assertEqual(self.send_packet("false", 20, self.kexinit()[0], closed=True)[0], None)

    def test_a_packet_that_is_no_kexinit_goes_through(self):
        payload = bytes(16) + (10).to_bytes(4, "big") + b"ext-info-s"
        sent, padding, _, _ = self.send_packet("false", 21, payload)
        self.assertEqual(sent, payload)
        self.assertEqual(set(padding), {0x55})        # random padding, as upstream
        self.assertEqual(self.send_packet("false", 20, payload)[0], bytes(16) + (0).to_bytes(4, "big"))

    def test_a_kexinit_that_cannot_be_read_goes_through_unchanged(self):
        for payload in (b"", b"short", bytes(16) + b"\xff\xff\xff\xff" + b"ext-info-s"):
            with self.subTest(payload=payload):
                self.assertEqual(self.send_packet("false", 20, payload)[0], payload)

    def test_server_sig_algs_are_those_of_the_persona(self):
        self.assertEqual(self.send_ext_info("ssh-ed25519,sk-ssh-ed25519@openssh.com,rsa-sha2-256,ssh-rsa"),
                         [(b"server-sig-algs", b"ssh-ed25519,sk-ssh-ed25519@openssh.com,rsa-sha2-256,ssh-rsa")])

    def test_a_server_without_ext_info_sends_none(self):
        # OpenSSH before 7.2 and dropbear before 2020.79 send no EXT_INFO at all
        self.assertIsNone(self.send_ext_info(""))
        self.assertIsNone(self.send_ext_info("  "))

    def test_without_the_key_twisted_sends_its_own(self):
        self.assertEqual(self.send_ext_info(None), [(b"server-sig-algs", b"rsa-sha2-512,rsa-sha2-256,ssh-ed25519")])

    def connection_made(self, drop):
        """connectionMade of the patched HoneyPotSSHTransport with [ssh] drop_connections = drop (None:
        unset); returns (what it wrote, whether it closed the connection, whether it logged the connect,
        whether it set the login timeout, its startTime)."""
        start = self.transport.index("    def connectionMade(self)")
        end = self.transport.index("\n    def ", start + 10)
        body = textwrap.dedent(self.transport[start:end])

        class Tcp:
            def __init__(self):
                self.written, self.closed = [], False

            def write(self, data):
                self.written.append(data)

            def loseConnection(self):
                self.closed = True

        class Ciphers:
            def __init__(self, *names):
                pass

            def setKeys(self, *keys):
                pass

        class TransportModule:
            SSHCiphers = Ciphers

        class Uuid:
            @staticmethod
            def uuid4():
                return types.SimpleNamespace(hex="0123456789abcdef")

        class Transport:
            ourVersionString = b"SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13.5"
            auth_timeout = 120
            startTime = 0.0

            def __init__(self):
                self.transport, self.connects, self.timeouts = Tcp(), 0, []

            def _emit_connect(self):
                self.connects += 1

            def setTimeout(self, seconds):
                self.timeouts.append(seconds)

        config = FakeConfig({} if drop is None else {("ssh", "drop_connections"): drop})
        namespace = {"CowrieConfig": config, "transport": TransportModule, "uuid": Uuid, "time": time,
                     "ProtocolWrapper": type("ProtocolWrapper", (), {})}
        exec(compile(ast.parse(body), "<patched transport>", "exec"), namespace)  # noqa: S102
        server = Transport()
        namespace["connectionMade"](server)
        return (b"".join(server.transport.written), server.transport.closed, server.connects, server.timeouts,
                server.startTime)

    def test_a_persona_without_an_ssh_server_closes_before_the_banner(self):
        self.assertIn('CowrieConfig.getboolean("ssh", "drop_connections", fallback=False)', self.transport)
        before = time.time()
        written, closed, connects, timeouts, started = self.connection_made("true")
        # the connect is logged, nothing is sent, the connection is closed; its close has a duration
        self.assertEqual((written, closed, connects, timeouts), (b"", True, 1, []))
        self.assertGreaterEqual(started, before)

    def test_a_persona_with_an_ssh_server_sends_its_banner(self):
        for drop in (None, "false"):
            with self.subTest(drop=drop):
                written, closed, connects, timeouts, _ = self.connection_made(drop)
                self.assertEqual((written, closed, connects, timeouts),
                                 (b"SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13.5\r\n", False, 1, [120]))

    def test_render_names_ext_info_s_as_the_server_does(self):
        import configparser
        for persona in catalog.PERSONAS:
            if persona.ssh_profile is None:
                continue
            ident = draw(persona, 1)
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.read_string(render_cfg(persona, ident, Path("/tmp/r"), Path("/tmp/k")))
            with self.subTest(persona=persona.id):
                self.assertEqual(cfg.get("ssh", "ext_info_s"),
                                 "true" if ssh_profiles.ext_info_s(ssh_profiles.PROFILES[persona.ssh_profile],
                                                                   ident.release) else "false")

    def test_render_drops_the_connections_of_a_persona_without_an_ssh_server(self):
        import configparser
        for persona in catalog.PERSONAS:
            with self.subTest(persona=persona.id):
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read_string(render_cfg(persona, draw(persona, 1), Path("/tmp/r"), Path("/tmp/k")))
                self.assertEqual(cfg.getboolean("ssh", "drop_connections", fallback=False),
                                 persona.ssh_profile is None)
                # Cowrie keeps listening on 22 either way
                self.assertEqual(cfg.get("ssh", "listen_endpoints"), "tcp:22:interface=0.0.0.0")
                self.assertTrue(cfg.getboolean("ssh", "enabled"))
                if persona.ssh_profile is None:
                    # one host key for Cowrie's factory to start, never offered
                    self.assertEqual(cfg.get("ssh", "public_key_auth"), "ssh-ed25519")
                    self.assertEqual(cfg.get("ssh", "ed25519_private_key"), "/tmp/k/ssh_host_ed25519_key")
                    self.assertFalse(cfg.has_option("ssh", "version"))

    def test_render_names_both_per_release(self):
        import configparser
        for persona in catalog.PERSONAS:
            if persona.ssh_profile is None:
                continue
            with self.subTest(persona=persona.id):
                ident = draw(persona, 1)
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read_string(render_cfg(persona, ident, Path("/tmp/r"), Path("/tmp/k")))
                profile = ssh_profiles.PROFILES[persona.ssh_profile]
                self.assertEqual(cfg.get("ssh", "kex_algorithms"),
                                 ",".join(ssh_profiles.kex_algorithms(profile, ident.release)))
                self.assertEqual(cfg.get("ssh", "server_sig_algs"),
                                 ssh_profiles.server_sig_algs(profile, ident.release))

    def test_fails_loudly_on_unknown_upstream(self):
        cases = (("src/cowrie/ssh/factory.py", "        if not self.primes:\n            ske = t.supportedKeyExchanges[:]\n",
                  "        if self.primes is None:\n            ske = t.supportedKeyExchanges[:]\n"),
                 ("src/cowrie/ssh/transport.py",
                  "    def _unsupportedVersionReceived(self, remoteVersion: bytes) -> None:\n",
                  "    def _unsupportedVersionReceived(self, remoteVersion) -> None:\n"),
                 ("src/cowrie/ssh/transport.py", '        self.transport.write(self.ourVersionString + b"\\r\\n")\n',
                  '        self.transport.write(self.ourVersionString + b"\\n")\n'))
        for rel, old, new in cases:
            with self.subTest(rel=rel):
                root = tmpdir(self) / "cowrie"
                shutil.copytree(FIXTURE, root)
                path = root / rel
                text = path.read_text()
                self.assertEqual(text.count(old), 1)
                path.write_text(text.replace(old, new))
                done = subprocess.run([sys.executable, str(PATCH), "--cowrie-root", str(root)],
                                      capture_output=True, text=True)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("Could not find expected text", done.stderr)



class ValidateKexTest(unittest.TestCase):
    def problems(self, persona_id, **changes):
        import configparser

        from tpot_personas import validate
        persona = catalog.by_id(persona_id)
        ident = draw(persona, 2)
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string(render_cfg(persona, ident, Path("/tmp/r"), Path("/tmp/k")))
        for key, value in changes.items():
            cfg.set("ssh", key, value)
        out = []
        validate._check_kex(ssh_profiles.PROFILES[persona.ssh_profile], ident.release, cfg, out)
        return out

    def test_the_rendered_lists_validate(self):
        for persona in catalog.PERSONAS:
            if persona.ssh_profile is None:
                continue
            with self.subTest(persona=persona.id):
                self.assertEqual(self.problems(persona.id), [])

    def ssh_problems(self, persona_id, **changes):
        """validate._check_ssh of the rendered [ssh] with these keys set (None: removed)."""
        import configparser

        from tpot_personas import validate
        persona = catalog.by_id(persona_id)
        ident = draw(persona, 2)
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string(render_cfg(persona, ident, Path("/tmp/r"), Path("/tmp/k")))
        for key, value in changes.items():
            if value is None:
                cfg.remove_option("ssh", key)
            else:
                cfg.set("ssh", key, value)
        out = []
        validate._check_ssh(persona, ident, cfg, out)
        return out

    def test_a_persona_without_an_ssh_server_has_no_ssh_client(self):
        import configparser

        from tpot_personas import validate
        persona = catalog.by_id("dlink-dir859")
        ident = draw(persona, 2)
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string(render_cfg(persona, ident, Path("/tmp/r"), Path("/tmp/k")))
        skip = [e for e in cfg.get("shell", "skip_python_commands").split(",") if e not in ("ssh", "/usr/bin/ssh")]
        cfg.set("shell", "skip_python_commands", ",".join(skip))
        out = []
        validate._check_ssh(persona, ident, cfg, out)
        self.assertEqual(out, ["ssh: the persona has no ssh client, Cowrie's ssh answers /usr/bin/ssh, ssh"])

    def test_a_persona_without_an_ssh_server_drops_and_names_no_server(self):
        self.assertEqual(self.ssh_problems("dlink-dir859"), [])
        self.assertEqual(self.ssh_problems("dlink-dir859", drop_connections=None),
                         ["ssh: the persona has no SSH server, drop_connections is not true"])
        self.assertEqual(self.ssh_problems("dlink-dir859", version="SSH-2.0-dropbear_2012.55"),
                         ["ssh: the persona has no SSH server, version is set"])
        # a persona with an SSH server never drops its connections
        self.assertEqual(self.ssh_problems("openwrt", drop_connections="true"),
                         ["ssh: drop_connections is true, the persona has the SSH server dropbear-openwrt"])
        self.assertEqual(self.ssh_problems("openwrt", drop_connections="false"), [])
        # read as Cowrie reads it (getboolean): yes and on drop too, a value it cannot read is named
        self.assertEqual(self.ssh_problems("openwrt", drop_connections="yes"),
                         ["ssh: drop_connections is true, the persona has the SSH server dropbear-openwrt"])
        self.assertEqual(self.ssh_problems("dlink-dir859", drop_connections="on"), [])
        self.assertEqual(self.ssh_problems("dlink-dir859", drop_connections="maybe"),
                         ["ssh: drop_connections 'maybe' is no boolean",
                          "ssh: the persona has no SSH server, drop_connections is not true"])
        # the options of an SSH session name a server too
        self.assertEqual(self.ssh_problems("dlink-dir859", sftp_enabled="true"),
                         ["ssh: the persona has no SSH server, sftp_enabled is set"])

    def test_a_newer_or_unknown_kex_is_found(self):
        out = self.problems("debian-trixie", kex_algorithms="curve25519-sha256,mlkem768x25519-sha256")
        self.assertEqual(out[0], "key exchange not supported: mlkem768x25519-sha256")
        self.assertTrue(out[1].startswith("key exchanges curve25519-sha256,mlkem768x25519-sha256 differ from openssh of "
                                          "SSH-2.0-OpenSSH_10.0p2 Debian-"), out)
        self.assertEqual(len(out), 2)

    def test_curve25519_is_required_and_sig_algs_checked(self):
        out = self.problems("centos-7", kex_algorithms="diffie-hellman-group14-sha1", server_sig_algs="ssh-rsa")
        self.assertIn("key exchanges offer no curve25519: modern clients cannot connect", out)
        self.assertIn("server-sig-algs ssh-rsa differ from openssh of SSH-2.0-OpenSSH_7.4", out)


if __name__ == "__main__":
    unittest.main()
