"""Configuration tests use subprocesses so module-level timezone stays isolated."""
import json
import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class TimezoneTests(unittest.TestCase):
    def inspect(self, zone):
        env = {**os.environ, 'HEALTH_TIMEZONE': zone}
        result = subprocess.run([sys.executable, '-c', '''import json, importlib.util
from datetime import datetime
s=importlib.util.spec_from_file_location('receiver', 'server/health_ingest.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
print(json.dumps({'zone':str(m.TZ),'date':str(m.local_date(datetime.fromisoformat('2025-01-15T20:00:00+00:00')))}))
'''], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    def test_east_asia_cross_midnight(self):
        self.assertEqual(self.inspect('Asia/Shanghai'), {'zone':'Asia/Shanghai','date':'2025-01-16'})
    def test_utc(self):
        self.assertEqual(self.inspect('UTC'), {'zone':'UTC','date':'2025-01-15'})
