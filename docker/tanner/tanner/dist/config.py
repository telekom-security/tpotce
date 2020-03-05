import configparser
import logging
import os
import sys

LOGGER = logging.getLogger(__name__)

config_template = {'DATA': {'db_config': '/opt/tanner/db/db_config.json',
                            'dorks': '/opt/tanner/data/dorks.pickle',
                            'user_dorks': '/opt/tanner/data/user_dorks.pickle',
                            'crawler_stats': '/opt/tanner/data/crawler_user_agents.txt',
                            'geo_db': '/opt/tanner/db/GeoLite2-City.mmdb',
                            'tornado': '/opt/tanner/data/tornado.py',
                            'mako': '/opt/tanner/data/mako.py'
                            },                            
                   'TANNER': {'host': 'tanner', 'port': 8090},
                   'WEB': {'host': 'tanner_web', 'port': 8091},
                   'API': {'host': 'tanner_api', 'port': 8092, 'auth': False, 'auth_signature': 'tanner_api_auth'},
                   'PHPOX': {'host': 'tanner_phpox', 'port': 8088},
                   'REDIS': {'host': 'tanner_redis', 'port': 6379, 'poolsize': 80, 'timeout': 1},
                   'EMULATORS': {'root_dir': '/opt/tanner'},
                   'EMULATOR_ENABLED': {'sqli': True, 'rfi': True, 'lfi': False, 'xss': True, 'cmd_exec': False,
                                        'php_code_injection': True, 'php_object_injection': True, "crlf": True,
                                        'xxe_injection': True, 'template_injection': False},
                   'SQLI': {'type': 'SQLITE', 'db_name': 'tanner_db', 'host': 'localhost', 'user': 'root',
                            'password': 'user_pass'},
                   'XXE_INJECTION': {'OUT_OF_BAND': False},
                   'RFI': {"allow_insecure": True},
                   'DOCKER': {'host_image': 'busybox:latest'},
                   'LOGGER': {'log_debug': '/tmp/tanner/tanner.log', 'log_err': '/tmp/tanner/tanner.err'},
                   'MONGO': {'enabled': False, 'URI': 'mongodb://localhost'},
                   'HPFEEDS': {'enabled': False, 'HOST': 'localhost', 'PORT': 10000, 'IDENT': '', 'SECRET': '',
                               'CHANNEL': 'tanner.events'},
                   'LOCALLOG': {'enabled': True, 'PATH': '/var/log/tanner/tanner_report.json'},
                   'CLEANLOG': {'enabled': False},
                   'REMOTE_DOCKERFILE': {'GITHUB': "https://raw.githubusercontent.com/mushorg/tanner/master/docker/"
                                                   "tanner/template_injection/Dockerfile"},
                   'SESSIONS': {"delete_timeout": 300}
                   }


class TannerConfig():
    config = None

    @staticmethod
    def set_config(config_path):
        cfg = configparser.ConfigParser()
        if not os.path.exists(config_path):
            print("Config file {} doesn't exist. Check the config path or use default".format(config_path))
            sys.exit(1)

        cfg.read(config_path)
        TannerConfig.config = cfg

    @staticmethod
    def get(section, value):
        res = None
        if TannerConfig.config is not None:
            try:
                convert_type = type(config_template[section][value])
                if convert_type is bool:
                    res = TannerConfig.config.getboolean(section, value)
                else:
                    res = convert_type(TannerConfig.config.get(section, value))
            except (configparser.NoOptionError, configparser.NoSectionError):
                LOGGER.warning("Error in config, default value will be used. Section: %s Value: %s", section, value)
                res = config_template[section][value]

        else:
            res = config_template[section][value]
        return res

    @staticmethod
    def get_section(section):
        res = {}
        if TannerConfig.config is not None:
            try:
                sec = TannerConfig.config[section]
                for k, v in sec.items():
                    convert_type = type(config_template[section][k])
                    if convert_type is bool:
                        res[k] = TannerConfig.config[section].getboolean(k)
                    else:
                        res[k] = convert_type(v)
            except (configparser.NoOptionError, configparser.NoSectionError):
                LOGGER.warning("Error in config, default value will be used. Section: %s Value: %s", section)
                res = config_template[section]

        else:
            res = config_template[section]

        return res
