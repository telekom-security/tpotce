# Copyright (C) 2018 Andre Vorbach @vorband
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging
import os
import gevent

import botocore.session, botocore.client
from botocore.exceptions import ClientError

from glastopf.modules.reporting.auxiliary.base_logger import BaseLogger


logger = logging.getLogger(__name__)


class S3Logger(BaseLogger):

    def __init__(self, data_dir, work_dir, config="glastopf.cfg", reconnect=True):
        config = os.path.join(work_dir, config)
        BaseLogger.__init__(self, config)
        self.files_dir = os.path.join(data_dir, 'files/')
        self.enabled = False
        self._initial_connection_happend = False
        self.options = {'enabled': self.enabled}
        if self.config.getboolean("s3storage", "enabled"):
            self.endpoint = self.config.get("s3storage", "endpoint")
            self.accesskey = self.config.get("s3storage", "aws_access_key_id")
            self.secretkey = self.config.get("s3storage", "aws_secret_access_key")
            self.version = self.config.get("s3storage", "signature_version")
            self.region = self.config.get("s3storage", "region")
            self.bucket = self.config.get("s3storage", "bucket")
            self.enabled = True
            self.options = {'enabled': self.enabled}
            self.s3client = None
            self.s3session = None
            gevent.spawn(self._start_connection, self.endpoint, self.accesskey, self.secretkey, self.version, self.region, self.bucket)

    def _start_connection(self, endpoint, accesskey, secretkey, version, region, bucket):
        self.s3session = botocore.session.get_session()
        self.s3session.set_credentials(accesskey, secretkey)
        self.s3client = self.s3session.create_client(
            's3',
            endpoint_url=self.endpoint,
            region_name=self.region,
            config=botocore.config.Config(signature_version=self.version)
        )
        self._initial_connection_happend = True

    def insert(self, attack_event):
        if self._initial_connection_happend:
            if attack_event.file_name is not None:
                with file(os.path.join(self.files_dir, attack_event.file_name), 'r') as file_handler:
                    try:
                        self.s3client.put_object(Bucket=self.bucket, Body=file_handler, Key=attack_event.sensorid+"/"+attack_event.file_name)
                        logger.debug('Sending file ({0}) using s3 bucket "{1}" on {2}'.format(attack_event.file_name, self.bucket, self.endpoint))
                    except ClientError as e:
                        logger.warning("Received error: %s", e.response['Error']['Message'])
        else:
            logger.warning('Not storing attack file because initial s3 connect has not succeeded')
