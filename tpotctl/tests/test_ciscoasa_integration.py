"""Ciscoasa v2 log inputs, dashboard references and packaged export consistency."""
import json
import re
import subprocess
import tempfile
from pathlib import Path
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / 'docker/tpotinit/dist/etc/objects/kibana_export.ndjson'

class CiscoasaIntegrationTest(unittest.TestCase):
    def test_data_modes_on_existing_files(self):
        text = (ROOT / 'docker/tpotinit/dist/bin/clean.sh').read_text()
        function = re.search(r'fuCISCOASA \(\) \{.*?^\}', text, re.M | re.S).group()
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / 'data'
            folder = data / 'ciscoasa'
            payloads = folder / 'payloads'
            payloads.mkdir(parents=True)
            sample = payloads / 'sample'
            sample.write_text('inert')
            sample.chmod(0o770)
            function = function.replace('/data/ciscoasa', str(folder))
            subprocess.run(['bash', '-c', 'chown() { :; }\nmyPERSISTENCE=on\n' + function + '\nfuCISCOASA'], check=True, capture_output=True)
            self.assertEqual(sample.stat().st_mode & 0o777, 0o660)
            for child in (folder, payloads, folder / 'identity', folder / 'log'):
                self.assertEqual(child.stat().st_mode & 0o777, 0o770)
            other = data / 'other'
            other.mkdir()
            target = Path(directory) / 'target'
            target.write_text('inert')
            target.chmod(0o600)
            (other / 'link').symlink_to(target)
            sample.chmod(0o660)
            command = 'find /data -path /data/ciscoasa -prune -o ! -type l -exec chmod 770 {} +'
            self.assertIn(command, text)
            subprocess.run(['bash', '-c', command.replace('/data', str(data))], check=True, capture_output=True)
            self.assertEqual(sample.stat().st_mode & 0o777, 0o660)
            self.assertEqual(other.stat().st_mode & 0o777, 0o770)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_both_logstash_pipelines_decode_json_without_kv(self):
        for name in ('logstash.conf', 'http_output.conf'):
            text = (ROOT / 'docker/elk/logstash/dist' / name).read_text()
            block = text.split('# Ciscoasa', 1)[1].split('# Citrix', 1)[0]
            self.assertIn('/data/ciscoasa/log/ciscoasa.json', block)
            self.assertIn('codec => json', block)
            block = text.split('filter {', 1)[1].split('# Ciscoasa', 1)[1].split('# Citrix', 1)[0]
            self.assertNotIn('kv {', block)
            self.assertIn('ISO8601', block)

    def test_dashboard_panels_reference_imported_objects(self):
        objects = [json.loads(line) for line in EXPORT.read_text().splitlines()]
        ids = {(o.get('type'), o.get('id')) for o in objects}
        dashboard = next(o for o in objects if o.get('type') == 'dashboard' and o['attributes']['title'] == 'Ciscoasa')
        refs = {r['name']: r for r in dashboard['references']}
        for panel in json.loads(dashboard['attributes']['panelsJSON']):
            name = panel['panelIndex'] + ':' + panel['panelRefName']
            self.assertIn(name, refs)
            self.assertIn((refs[name]['type'], refs[name]['id']), ids)
        lenses = [o for o in objects if o.get('type') == 'lens' and o['attributes']['title'].startswith('Ciscoasa ')]
        fields = {c.get('sourceField') for o in lenses
                  for layer in o['attributes']['state']['datasourceStates']['formBased']['layers'].values()
                  for c in layer['columns'].values()}
        self.assertTrue({'event_type.keyword', 'cve.keyword', 'username.keyword', 'password.keyword',
                         'asa.group.keyword', 'asa.client.version.keyword', 'asa.ike.vendor_ids.keyword',
                         'asa.ike.group_name.keyword', 'asa.upload.sha256.keyword', 'asa.chain_stage.keyword'} <= fields)
        for o in objects:
            if o.get('attributes', {}).get('title') in ('Username Tagcloud', 'Password Tagcloud'):
                self.assertIn('Ciscoasa', o['attributes']['state']['query']['query'])

    def test_export_zip_contains_exact_ndjson(self):
        with zipfile.ZipFile(str(EXPORT) + '.zip') as archive:
            self.assertEqual(archive.read(EXPORT.name), EXPORT.read_bytes())

    def test_smoke_harness_resolves_dynamic_ports_after_restart(self):
        text = (ROOT / 'docker/_tests/tests/ciscoasa.sh').read_text()
        after = text.split('test_compose restart ciscoasa', 1)[1]
        for port in ('8443', '500/udp', '4500/udp'):
            self.assertIn('test_get_mapped_port ciscoasa ' + port, after)

if __name__ == '__main__':
    unittest.main()
