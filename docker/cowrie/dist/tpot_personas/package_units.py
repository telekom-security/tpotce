"""What the maintainer scripts of a server's packages do to systemd ([shell] package_units, read by the patched
systemctl when apt, dpkg, yum, dnf or rpm of the session install or remove a package): the units of the persona
each installed package owns, the services the common server packages ship and what starting them runs.

Recorded in containers with systemd (refs t12: debian:12, ubuntu:24.04 and almalinux:9 with systemd as PID 1,
centos:7 without; the services of one container installed one after another):
- Debian and Ubuntu: the postinst enables a service it ships and starts it ("Created symlink" lines of
  deb-systemd-helper, each with a blank line after it); remove stops it (prerm) and takes its unit file, the
  links in /etc/systemd/system stay until purge; a service of a package whose binary is another package's
  (nginx-common) stops when that package goes (nginx).
- Red Hat: the post scriptlet runs systemctl preset (enabled for the units the preset files name, sshd and
  tuned, else disabled); remove disables and stops (dnf 4 and rpm 4.16 print the "Removed" lines of systemctl,
  CentOS 7's scriptlets write nothing).
- Removing inetutils-telnetd takes its line out of /etc/inetd.conf (update-inetd), inetd stays and no longer
  listens on 23.
- A program a service runs, its user and its sockets as the recordings show them; unverified: the sizes of the
  processes, the unit texts beyond the keys recorded (Description, Documentation, After, Type, ExecStart, User,
  KillMode, Alias, WantedBy), httpd's children and its socket (it did not start next to nginx).
"""
import functools
import gzip
import json
from pathlib import Path

from . import system
from .servers import unit_keys

PACKAGE_UNITS = "package-units.json"

# the index rows (apt_meta's columns) of the covered service packages (apt-cache show and policy of debian:12,
#   debian:13, ubuntu:22.04 and ubuntu:24.04, arm64, 2026-10-19; refs t12 *-t12-show-services; unverified: the
#   sizes of amd64)
SERVICE_META = {
    "bookworm": {
        "nginx": ["1.22.1-9+deb12u10", "main", "security", 1343, 497760, "-",
            "httpd", "Debian Nginx Maintainers <pkg-nginx-maintainers@alioth-lists.debian.net>", "optional", "-", "small, powerful, scalable web/proxy server"],
        "nginx-common": ["1.22.1-9+deb12u10", "main", "security", 332, 114932, "all",
            "httpd", "Debian Nginx Maintainers <pkg-nginx-maintainers@alioth-lists.debian.net>", "optional", "-", "small, powerful, scalable web/proxy server - common files"],
        "apache2": ["2.4.68-1~deb12u1", "main", "-", 584, 232900, "-",
            "httpd", "Debian Apache Maintainers <debian-apache@lists.debian.org>", "optional", "-", "Apache HTTP Server"],
        "apache2-bin": ["2.4.68-1~deb12u1", "main", "-", 10172, 1294256, "-",
            "httpd", "Debian Apache Maintainers <debian-apache@lists.debian.org>", "optional", "-", "Apache HTTP Server (modules and other binary files)"],
        "apache2-data": ["2.4.68-1~deb12u1", "main", "-", 847, 160148, "all",
            "httpd", "Debian Apache Maintainers <debian-apache@lists.debian.org>", "optional", "-", "Apache HTTP Server (common files)"],
        "apache2-utils": ["2.4.68-1~deb12u1", "main", "-", 868, 215728, "-",
            "httpd", "Debian Apache Maintainers <debian-apache@lists.debian.org>", "optional", "-", "Apache HTTP Server (utility programs for web servers)"],
        "redis-server": ["5:7.0.15-1~deb12u10", "main", "security", 183, 73908, "-",
            "database", "Chris Lamb <lamby@debian.org>", "optional", "-", "Persistent key-value database with network interface"],
        "redis-tools": ["5:7.0.15-1~deb12u10", "main", "security", 6112, 899140, "-",
            "database", "Chris Lamb <lamby@debian.org>", "optional", "-", "Persistent key-value database with network interface (client)"],
        "mariadb-server": ["1:10.11.19-0+deb12u1", "main", "security", 54477, 3464624, "-",
            "database", "Debian MySQL Maintainers <pkg-mysql-maint@lists.alioth.debian.org>", "optional", "-", "MariaDB database server binaries"],
        "mariadb-client": ["1:10.11.19-0+deb12u1", "main", "security", 63150, 2884408, "-",
            "database", "Debian MySQL Maintainers <pkg-mysql-maint@lists.alioth.debian.org>", "optional", "-", "MariaDB database client binaries"],
        "mariadb-common": ["1:10.11.19-0+deb12u1", "main", "security", 69, 27624, "all",
            "database", "Debian MySQL Maintainers <pkg-mysql-maint@lists.alioth.debian.org>", "optional", "-", "MariaDB common configuration files"],
        "mariadb-server-core": ["1:10.11.19-0+deb12u1", "main", "security", 44847, 6828820, "-",
            "database", "Debian MySQL Maintainers <pkg-mysql-maint@lists.alioth.debian.org>", "optional", "-", "MariaDB database core server files"],
        "mysql-common": ["5.8+1.1.0", "main", "-", 33, 6636, "all",
            "database", "Debian MySQL Maintainers <pkg-mysql-maint@lists.alioth.debian.org>", "optional", "-", "MySQL database common files, e.g. /etc/mysql/my.cnf"],
    },
    "trixie": {
        "nginx": ["1.26.3-3+deb13u9", "main", "security", 1604, 571828, "-",
            "httpd", "Debian Nginx Maintainers <pkg-nginx-maintainers@alioth-lists.debian.net>", "optional", "-", "small, powerful, scalable web/proxy server"],
        "nginx-common": ["1.26.3-3+deb13u9", "main", "security", 305, 111548, "all",
            "httpd", "Debian Nginx Maintainers <pkg-nginx-maintainers@alioth-lists.debian.net>", "optional", "-", "small, powerful, scalable web/proxy server - common files"],
        "apache2": ["2.4.69-1~deb13u1", "main", "security", 582, 230040, "-",
            "httpd", "Debian Apache Maintainers <debian-apache@lists.debian.org>", "optional", "-", "Apache HTTP Server"],
        "apache2-bin": ["2.4.69-1~deb13u1", "main", "security", 10110, 1305420, "-",
            "httpd", "Debian Apache Maintainers <debian-apache@lists.debian.org>", "optional", "-", "Apache HTTP Server (modules and other binary files)"],
        "apache2-data": ["2.4.69-1~deb13u1", "main", "security", 847, 160016, "all",
            "httpd", "Debian Apache Maintainers <debian-apache@lists.debian.org>", "optional", "-", "Apache HTTP Server (common files)"],
        "apache2-utils": ["2.4.69-1~deb13u1", "main", "security", 870, 219068, "-",
            "httpd", "Debian Apache Maintainers <debian-apache@lists.debian.org>", "optional", "-", "Apache HTTP Server (utility programs for web servers)"],
        "redis-server": ["5:8.0.2-3+deb13u3", "main", "security", 178, 67364, "-",
            "database", "Chris Lamb <lamby@debian.org>", "optional", "-", "Persistent key-value database with network interface"],
        "redis-tools": ["5:8.0.2-3+deb13u3", "main", "security", 7199, 1149976, "-",
            "database", "Chris Lamb <lamby@debian.org>", "optional", "-", "Persistent key-value database with network interface (client)"],
        "mariadb-server": ["1:11.8.6-0+deb13u1", "main", "-", 55824, 3651012, "-",
            "database", "Debian MySQL Maintainers <pkg-mysql-maint@lists.alioth.debian.org>", "optional", "-", "MariaDB database server binaries"],
        "mariadb-client": ["1:11.8.6-0+deb13u1", "main", "-", 64767, 3052008, "-",
            "database", "Debian MySQL Maintainers <pkg-mysql-maint@lists.alioth.debian.org>", "optional", "-", "MariaDB database client binaries"],
        "mariadb-common": ["1:11.8.6-0+deb13u1", "main", "-", 70, 29544, "all",
            "database", "Debian MySQL Maintainers <pkg-mysql-maint@lists.alioth.debian.org>", "optional", "-", "MariaDB database common config files (/etc/mysql/mariadb.conf.d/)"],
        "mariadb-server-core": ["1:11.8.6-0+deb13u1", "main", "-", 47157, 7382080, "-",
            "database", "Debian MySQL Maintainers <pkg-mysql-maint@lists.alioth.debian.org>", "optional", "-", "MariaDB database core server files"],
        "mysql-common": ["5.8+1.1.1", "main", "-", 33, 6784, "all",
            "database", "Debian MySQL Maintainers <pkg-mysql-maint@lists.alioth.debian.org>", "optional", "-", "MySQL database common files, e.g. /etc/mysql/my.cnf"],
    },
    "jammy": {
        "nginx": ["1.18.0-6ubuntu14.21", "main", "updates", 51, 3880, "-",
            "web", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "small, powerful, scalable web/proxy server"],
        "nginx-common": ["1.18.0-6ubuntu14.21", "main", "updates", 285, 40516, "all",
            "httpd", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "small, powerful, scalable web/proxy server - common files"],
        "apache2": ["2.4.52-1ubuntu4.25", "main", "security", 538, 97894, "-",
            "web", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "Apache HTTP Server"],
        "apache2-bin": ["2.4.52-1ubuntu4.25", "main", "security", 4564, 1317960, "-",
            "httpd", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "Apache HTTP Server (modules and other binary files)"],
        "apache2-data": ["2.4.52-1ubuntu4.25", "main", "security", 856, 164882, "all",
            "httpd", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "Apache HTTP Server (common files)"],
        "apache2-utils": ["2.4.52-1ubuntu4.25", "main", "security", 345, 89280, "-",
            "net", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "Apache HTTP Server (utility programs for web servers)"],
        "redis-server": ["5:6.0.16-1ubuntu1.2", "universe", "updates", 191, 45864, "-",
            "universe/misc", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "Persistent key-value database with network interface"],
        "redis-tools": ["5:6.0.16-1ubuntu1.2", "universe", "updates", 4116, 856500, "-",
            "universe/database", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "Persistent key-value database with network interface (client)"],
        "mariadb-server": ["1:10.6.23-0ubuntu0.22.04.1", "universe", "updates", 69, 11768, "all",
            "universe/database", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "MariaDB database server (metapackage depending on the latest version)"],
        "mariadb-client": ["1:10.6.23-0ubuntu0.22.04.1", "universe", "updates", 71, 13078, "all",
            "universe/database", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "MariaDB database client (metapackage depending on the latest version)"],
        "mariadb-common": ["1:10.6.23-0ubuntu0.22.04.1", "universe", "updates", 79, 16486, "all",
            "universe/database", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "MariaDB common configuration files"],
        "mysql-common": ["5.8+1.0.8", "main", "-", 34, 7212, "all",
            "database", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "MySQL database common files, e.g. /etc/mysql/my.cnf"],
    },
    "noble": {
        "nginx": ["1.24.0-2ubuntu7.18", "main", "updates", 1347, 521322, "-",
            "web", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "small, powerful, scalable web/proxy server"],
        "nginx-common": ["1.24.0-2ubuntu7.18", "main", "updates", 241, 45076, "all",
            "httpd", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "small, powerful, scalable web/proxy server - common files"],
        "apache2": ["2.4.58-1ubuntu8.17", "main", "security", 457, 90228, "-",
            "web", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "Apache HTTP Server"],
        "apache2-bin": ["2.4.58-1ubuntu8.17", "main", "security", 9992, 1328618, "-",
            "httpd", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "Apache HTTP Server (modules and other binary files)"],
        "apache2-data": ["2.4.58-1ubuntu8.17", "main", "security", 857, 163308, "all",
            "httpd", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "Apache HTTP Server (common files)"],
        "apache2-utils": ["2.4.58-1ubuntu8.17", "main", "security", 748, 99566, "-",
            "net", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "Apache HTTP Server (utility programs for web servers)"],
        "redis-server": ["5:7.0.15-1ubuntu0.24.04.5", "universe", "updates", 183, 51686, "-",
            "universe/misc", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "Persistent key-value database with network interface"],
        "redis-tools": ["5:7.0.15-1ubuntu0.24.04.5", "universe", "updates", 6624, 1170186, "-",
            "universe/database", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "Persistent key-value database with network interface (client)"],
        "mariadb-server": ["1:10.11.14-0ubuntu0.24.04.1", "universe", "updates", 56318, 3441640, "-",
            "universe/database", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "MariaDB database server binaries"],
        "mariadb-client": ["1:10.11.14-0ubuntu0.24.04.1", "universe", "updates", 64428, 2477496, "-",
            "universe/database", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "MariaDB database client binaries"],
        "mariadb-common": ["1:10.11.14-0ubuntu0.24.04.1", "universe", "updates", 69, 28550, "all",
            "universe/database", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "MariaDB database common files (e.g. /etc/mysql/mariadb.conf.d/)"],
        "mariadb-server-core": ["1:10.11.14-0ubuntu0.24.04.1", "universe", "updates", 46433, 8286128, "-",
            "universe/database", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "MariaDB database core server files"],
        "mysql-common": ["5.8+1.1.0build1", "main", "-", 33, 6746, "all",
            "database", "Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>", "optional", "-", "MySQL database common files, e.g. /etc/mysql/my.cnf"],
    },
}


# the unit of the persona each package owns (removing the package takes it); the package must be installed
DEB_UNIT_OWNERS = {"ssh.service": "openssh-server", "ssh.socket": "openssh-server", "inetd.service": "openbsd-inetd",
                   "cron.service": "cron", "rsyslog.service": "rsyslog",
                   "systemd-timesyncd.service": "systemd-timesyncd", "systemd-resolved.service": "systemd-resolved",
                   "snapd.service": "snapd", "irqbalance.service": "irqbalance",
                   "unattended-upgrades.service": "unattended-upgrades", "NetworkManager.service": "network-manager",
                   "NetworkManager-wait-online.service": "network-manager", "avahi-daemon.service": "avahi-daemon",
                   "bluetooth.service": "bluez", "ModemManager.service": "modemmanager",
                   "wpa_supplicant.service": "wpasupplicant", "triggerhappy.service": "triggerhappy"}
EL_UNIT_OWNERS = {"sshd.service": "openssh-server", "tuned.service": "tuned", "telnet.socket": "telnet-server",
                  "telnet@.service": "telnet-server", "postfix.service": "postfix", "chronyd.service": "chrony",
                  "crond.service": "cronie", "rsyslog.service": "rsyslog", "NetworkManager.service": "NetworkManager",
                  "NetworkManager-wait-online.service": "NetworkManager", "rhsmcertd.service": "subscription-manager",
                  "auditd.service": "audit", "polkit.service": "polkit", "kdump.service": "kexec-tools"}

# what installing a package does on each release, recorded in containers with systemd (refs t12 *-t12r2-*:
#   tools/build_package_installs.py): the packages an install brings, the postinst texts, dpkg -L / rpm -ql, the
#   unit files of the covered services, the units the presets enable
INSTALLS = Path(__file__).resolve().parent / "package_installs.json.gz"


@functools.cache
def installs():
    with gzip.open(INSTALLS, "rt", encoding="utf-8") as f:
        return json.load(f)


def deb_release(persona, ident):
    """The recorded data of the persona's Debian / Ubuntu release (Raspberry Pi OS: bookworm's), {} elsewhere."""
    if not system.has_dpkg(persona) or persona.kind != "server":
        return {}
    return installs()["deb"].get("bookworm" if persona.family == "raspios" else system.codename(persona, ident), {})


def _exec_programs(text):
    """The programs a unit starts its service with: those of ExecStartPre and ExecStart."""
    keys = unit_keys(text)
    return [value.lstrip("-@+!:").split()[0] for key in ("ExecStartPre", "ExecStart") for value in keys.get(key, [])
            if value.lstrip("-@+!:").strip()]


def _units_of(files, texts):
    """[{name, path, text}] of the unit files among a package's files whose text was recorded."""
    found = []
    for path in files:
        name = path.rsplit("/", 1)[-1]
        if "/systemd/system/" in path and name in texts:
            found.append({"name": name, "path": path, "text": texts[name]})
    return found


def _add_stops(services, files, texts):
    """The units of other packages a package's removal stops: those whose ExecStartPre / ExecStart program it ships
    (jammy's nginx-core, RHEL's nginx-core: the binary of nginx.service)."""
    owner_of = {path: package for package, paths in files.items() for path in paths}
    for package, paths in files.items():
        units = _units_of(paths, texts)
        if units:
            services.setdefault(package, {})["units"] = units
            for unit in units:
                for program in _exec_programs(unit["text"]):
                    owner = next((owner_of[p] for p in system.path_spellings(program) if p in owner_of), None)
                    if owner and owner != package:
                        stops = services.setdefault(owner, {}).setdefault("stops", [])
                        if unit["name"] not in stops:
                            stops.append(unit["name"])
    return services


def _deb_services(persona, ident):
    """{package: what installing it does}: the packages it brings (with, in the order of the recording), the units
    it ships, the units its postinst starts, the units of other packages its removal stops (its program in their
    ExecStart). The postinst texts are the release's blocks."""
    release = deb_release(persona, ident)
    texts, files = release.get("units", {}), release.get("files", {})
    services = {}
    for package, install in release.get("installs", {}).items():
        services[package] = {"with": [row[0] for row in install["new"] if row[0] != package],
                             "start": list(install["active"])}
    return _add_stops(services, files, texts)


def _el_services(persona):
    """{package: what dnf / yum 3 installs with it}: its dependencies (weak ones apart), its files (rpm -ql), the
    units it ships and those of other packages its removal stops."""
    data = installs()["rhel" if persona.family == "rhel" else "centos"]
    texts, files = data.get("units", {}), data.get("files", {})
    services = {}
    for package, install in data.get("installs", {}).items():
        services[package] = {"with": list(install["deps"]), "weak": list(install["weak"])}
    for package, paths in files.items():
        services.setdefault(package, {})["files"] = list(paths)
    return _add_stops(services, files, texts)


def _profiles(persona, ident):
    """{unit: what its start runs}: processes (user, command, vsz, rss, stat, count; the first the main one, the
    others its children; count "cpus" one per CPU), sockets (family, address, port, fd, backlog) of the main
    process and its children (a sixth field: of the processes of that entry alone), the account its package makes
    (name, gecos, home, shell, fixed uid or None)."""
    cpus = max(1, ident.hardware.cpus)
    if persona.family in ("rhel", "centos"):
        return {
            "nginx.service": {"processes": [["root", "nginx: master process /usr/sbin/nginx", 11484, 2264, "Ss", 1],
                                            ["nginx", "nginx: worker process", 15248, 4704, "S", cpus]],
                              "sockets": [[4, "0.0.0.0", 80, 6, 511], [6, "::", 80, 7, 511]],
                              "account": ["nginx", "Nginx web server", "/var/lib/nginx", "/sbin/nologin", None]},
            # (ref almalinux-9-t12r2-start-httpd; CentOS 7 centos-7-t12r2-run-httpd: five children of the prefork MPM)
            "httpd.service": {"processes": [["root", "/usr/sbin/httpd -DFOREGROUND", 29880, 10504, "Ss", 1],
                                            ["apache", "/usr/sbin/httpd -DFOREGROUND", 32124, 9000, "S", 1],
                                            ["apache", "/usr/sbin/httpd -DFOREGROUND", 2437544, 10472, "Sl", 1],
                                            ["apache", "/usr/sbin/httpd -DFOREGROUND", 2239912, 10252, "Sl", 2]],
                              "sockets": [[4, "0.0.0.0", 80, 3, 511]],
                              "account": ["apache", "Apache", "/usr/share/httpd", "/sbin/nologin", 48]}
            if persona.family == "rhel" else
            {"processes": [["root", "/usr/sbin/httpd -DFOREGROUND", 21052, 6860, "Ss", 1],
                           ["apache", "/usr/sbin/httpd -DFOREGROUND", 21052, 6356, "S", 5]],
             "sockets": [[6, "::", 80, 3, 511]],
             "account": ["apache", "Apache", "/usr/share/httpd", "/sbin/nologin", 48]},
            "redis.service": {"processes": [["redis", "/usr/bin/redis-server 127.0.0.1:6379", 59680, 9600, "Ssl", 1]],
                              "sockets": [[4, "127.0.0.1", 6379, 6, 511], [6, "::1", 6379, 7, 511]],
                              "account": ["redis", "Redis Database Server", "/var/lib/redis", "/sbin/nologin", None]},
            "mariadb.service": {"processes": [["mysql", "/usr/libexec/mariadbd --basedir=/usr", 1323292, 92416, "Ssl",
                                               1]],
                                "sockets": [[6, "::", 3306, 19, 80]],
                                "account": ["mysql", "MySQL Server", "/var/lib/mysql", "/sbin/nologin", 27]}
            if persona.family == "rhel" else
            # CentOS 7's MariaDB 5.5: mysqld_safe (by its script's name) and its mysqld, which alone listens
            #   (ref centos-7-t12r3-run-mariadb; the sizes are those of x86_64, the recording ran on arm64)
            {"processes": [["mysql", "/bin/sh /usr/bin/mysqld_safe --basedir=/usr", 113412, 1600, "Ss", 1],
                           ["mysql", "/usr/libexec/mysqld --basedir=/usr --datadir=/var/lib/mysql --plugin-dir="
                            "/usr/lib64/mysql/plugin --log-error=/var/log/mariadb/mariadb.log --pid-file=/var/run/"
                            "mariadb/mariadb.pid --socket=/var/lib/mysql/mysql.sock", 971856, 80724, "Sl", 1]],
             "sockets": [[4, "0.0.0.0", 3306, 14, 50, 1]],
             "account": ["mysql", "MariaDB Server", "/var/lib/mysql", "/sbin/nologin", 27]},
        }
    noble = system.codename(persona, ident) == "noble"
    return {
        "nginx.service": {"processes": [["root", "nginx: master process /usr/sbin/nginx -g daemon on; master_process on;",
                                         10356, 1868, "S", 1],
                                        ["www-data", "nginx: worker process", 12384, 4608, "S", cpus]],
                          "sockets": [[4, "0.0.0.0", 80, 5, 511], [6, "::", 80, 6, 511]]},
        "apache2.service": {"processes": [["root", "/usr/sbin/apache2 -k start", 6476, 4612, "Ss", 1],
                                          ["www-data", "/usr/sbin/apache2 -k start", 1997792, 4292, "Sl", 2]],
                            "sockets": [[4, "0.0.0.0", 80, 3, 511]]},
        "apache-htcacheclean.service": {"processes": [["www-data", "/usr/bin/htcacheclean -d 120 -p "
                                                       "/var/cache/apache2/mod_cache_disk -l 300M -n", 4232, 1572,
                                                       "Ss", 1]], "sockets": []},
        "redis-server.service": {"processes": [["redis", "/usr/bin/redis-server 127.0.0.1:6379", 67164, 10624, "Ssl",
                                                1]],
                                 "sockets": [[4, "127.0.0.1", 6379, 6, 511], [6, "::1", 6379, 7, 511]],
                                 "account": ["redis", "", "/var/lib/redis", "/usr/sbin/nologin", None]},
        "mariadb.service": {"processes": [["mysql", "/usr/sbin/mariadbd", 1288812, 84928, "Ssl", 1]],
                            "sockets": [[4, "127.0.0.1", 3306, 24 if noble else 21, 80]],
                            "account": ["mysql", "MariaDB Server,,," if noble else "MySQL Server,,,", "/nonexistent",
                                        "/bin/false", None]},
    }


def package_units(persona, ident, units):
    """The runtime data of the patched systemctl's package hook (PACKAGE_UNITS): the style of the maintainer
    scripts, the persona's units by their package (installed ones only), the telnet line of inetd, the services of
    the covered packages and what their start runs. units: systemd_units of the persona."""
    deb = system.has_dpkg(persona)
    if deb:
        installed = system.apt_packages(persona, ident)["installed"]
        table = dict(DEB_UNIT_OWNERS)
        table.update({"nvidia-persistenced.service": name for name in system.nvidia_packages(ident)
                      if name.startswith("nvidia-compute-utils-")})
    elif persona.family == "centos":
        installed, table = system.centos_installed(ident), EL_UNIT_OWNERS
    else:
        from .servers import dnf_packages
        installed, table = dnf_packages(persona, ident)["installed"], EL_UNIT_OWNERS
    names = {unit["name"] for unit in units.get("units", [])}
    owners = {}
    for unit, package in sorted(table.items()):
        if unit in names and package in installed:
            owners.setdefault(package, []).append(unit)
    data = {"style": "debian" if deb else persona.family, "owners": owners,
            "services": _deb_services(persona, ident) if deb else _el_services(persona),
            "profiles": _profiles(persona, ident),
            # the postinst texts of the release (deb_release blocks: "Setting up" to the next one)
            "blocks": deb_release(persona, ident).get("blocks", {}) if deb else {},
            # the units the presets of Red Hat enable (refs t12 almalinux-9-t12r2-state-*: sshd, tuned, chronyd, crond,
            #   rsyslog, NetworkManager, auditd, kdump; CentOS 7 taken as RHEL 9, unverified)
            "preset_enabled": [] if deb else list(installs()["rhel"]["enabled"])}
    if deb and "inetutils-telnetd" in installed:
        data["inetd"] = {"package": "inetutils-telnetd", "service": "telnet", "port": 23, "conf": "/etc/inetd.conf",
                         "line": "telnet\tstream\ttcp\tnowait\troot\t/usr/sbin/tcpd\t/usr/sbin/telnetd"}
    return data
