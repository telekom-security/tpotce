from setuptools import setup

with open("README.rst", "r") as f:
    long_description = f.read()

setup(
    name='honeypots',
    author='QeeqBox',
    author_email='gigaqeeq@gmail.com',
    description=r"23 different honeypots in a single pypi package! (dns, ftp, httpproxy, http, https, imap, mysql, pop3, postgres, redis, smb, smtp, socks5, ssh, telnet, vnc, mssql, elastic, ldap, ntp, memcache, snmp, oracle, sip and irc) ",
    long_description=long_description,
    version='0.51',
    license="AGPL-3.0",
    license_files=('LICENSE'),
    url="https://github.com/qeeqbox/honeypots",
    packages=['honeypots'],
    entry_points={
        "console_scripts": [
            'honeypots=honeypots.__main__:main_logic'
        ]
    },
    include_package_data=True,
    install_requires=[
        'pycrypto',
        'scapy',
        'twisted',
        'psutil',
        'psycopg2-binary',
        'requests',
        'impacket',
        'paramiko',
        'service_identity',
        'netifaces'
    ],
    extras_require={
        'test': ['redis', 'mysql-connector', 'elasticsearch', 'pymssql', 'ldap3', 'pysnmp']
    },
    python_requires='>=3.5'
)
