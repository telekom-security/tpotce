# Copyright (C) 2015 Lukas Rist
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

from ConfigParser import SafeConfigParser
import os


class BaseLogger(object):
    def __init__(self, config='glastopf.cfg'):
        if not isinstance(config, SafeConfigParser):
            self.config = SafeConfigParser(os.environ)
            self.config.read(config)
        else:
            self.config = config

    def insert(self, event):
        pass
