"""Manual integration probes for the four new ports in T-Pot standard/sensor."""

import ftplib
import socket
import ssl
import sys

import ldap3
import paho.mqtt.client as mqtt

host = sys.argv[1]
password = "AuditPass123"
context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE

for port, name in ((389, "ldap"), (636, "ldaps")):
    server = ldap3.Server(
        host,
        port=port,
        use_ssl=port == 636,
        tls=ldap3.Tls(validate=ssl.CERT_NONE),
        connect_timeout=5,
    )
    client = ldap3.Connection(
        server, user=f"cn=free-{name},dc=corp,dc=local", password=password, receive_timeout=5
    )
    assert not client.bind() and client.result["result"] == 49
    client.unbind()

client = ftplib.FTP_TLS(context=context)  # noqa: S321 - standard FTPS client for honeypot validation
client.sock = context.wrap_socket(
    socket.create_connection((host, 990), timeout=5), server_hostname=host
)
client.file = client.sock.makefile("r", encoding=client.encoding)
client.welcome = client.getresp()
try:
    client.login("free-ftps", password)
except ftplib.error_perm:
    pass
else:
    raise AssertionError("FTPS accepted authentication")
finally:
    client.close()

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="free-mqtts")
client.username_pw_set("free-mqtts", password)
client.tls_set_context(context)
client.tls_insecure_set(True)
client.connect(host, 8883)
client.loop(timeout=5)
client.disconnect()
print("LDAP, LDAPS, FTPS and MQTTS attempts sent with standard clients")
