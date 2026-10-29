import ast, concurrent.futures, importlib.util, json, pathlib, tempfile, threading, unittest, urllib.request, urllib.error
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
P=pathlib.Path(__file__).resolve().parents[1]
__import__('os').environ['HEALTH_TIMEZONE']='America/Toronto'
spec=importlib.util.spec_from_file_location('ingest',P/'server'/'health_ingest.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
# Exercise the real formatting functions without importing the AstrBot runtime.
tree=ast.parse((P/'main.py').read_text());ns={'Any':object,'Path':pathlib.Path,'datetime':datetime,'date':date,'timedelta':timedelta,'ZoneInfo':ZoneInfo,'os':__import__('os'),'json':json}
keep=[]
for n in tree.body:
 if isinstance(n,ast.FunctionDef):keep.append(n)
 elif isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'TZ','METRIC_LABELS','CUMULATIVE'} for t in n.targets):keep.append(n)
exec(compile(ast.Module(body=keep,type_ignores=[]),'plugin','exec'),ns)
class HealthTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=pathlib.Path(self.tmp.name);m.DATA_ROOT=root;m.HISTORY_DIR=root/'history';m.LEGACY_FILE=root/'health.json';m.TOKEN_FILE=root/'token';m.BASE=root;ns.update(DATA_ROOT=root,HISTORY_DIR=m.HISTORY_DIR,LEGACY_FILE=m.LEGACY_FILE)
 def tearDown(self):self.tmp.cleanup()
 def sleep(self,rows,day='2026-10-28'):return m.merge_sleep({'date':day,'samples':rows})
 def sample(self,stage='Asleep Core',a='2026-10-28T01:00:00-04:00',b='2026-10-28T02:00:00-04:00',**kw):return dict(value=stage,start=a,end=b,**kw)
 def metric(self,value=100,a='2026-10-28T01:00:00-04:00',b='2026-10-28T02:00:00-04:00',**kw):return dict(metric='steps',value=value,unit='count',start=a,end=b,**kw)
 def test_two_nights_split(self):
  r=self.sleep([self.sample(),self.sample(a='2026-10-27T01:00:00-04:00',b='2026-10-27T02:00:00-04:00')]);self.assertEqual(r['dates'],['2026-10-27','2026-10-28']);self.assertEqual(m._load_history('2026-10-28')['sleep']['asleep_seconds'],3600)
 def test_bed_and_sleep_preserved(self):
  self.sleep([self.sample('InBed'),self.sample()]);s=m._load_history('2026-10-28')['sleep'];self.assertEqual(s['sample_count'],2);self.assertEqual(s['in_bed_seconds'],3600)
 def test_stage_overlap(self):
  self.sleep([self.sample(),self.sample('Asleep Deep',a='2026-10-28T01:30:00-04:00',b='2026-10-28T02:30:00-04:00')]);s=m._load_history('2026-10-28')['sleep'];self.assertEqual(s['asleep_seconds'],5400);self.assertEqual(s['core_seconds']+s['deep_seconds'],5400)
 def test_awake_exclusion(self):
  self.sleep([self.sample(),self.sample('Awake',a='2026-10-28T01:30:00-04:00',b='2026-10-28T01:40:00-04:00')]);s=m._load_history('2026-10-28')['sleep'];self.assertEqual(s['asleep_seconds'],3000);self.assertEqual(s['awake_count'],1)
 def test_idempotence(self):
  self.sleep([self.sample()]);self.assertFalse(self.sleep([self.sample()])['changed'])
 def test_partial_merge(self):
  self.sleep([self.sample()]);self.sleep([self.sample(a='2026-10-28T03:00:00-04:00',b='2026-10-28T04:00:00-04:00')]);self.assertEqual(m._load_history('2026-10-28')['sleep']['asleep_seconds'],7200)
 def test_missing_stages(self):
  self.sleep([self.sample('Asleep')]);self.assertIsNone(m._load_history('2026-10-28')['sleep']['deep_seconds']);self.assertNotIn('深睡',ns['format_health'](m._load_history('2026-10-28')))
 def test_invalid_enum(self):
  for stage in [3.5,True,'garbage']:
   with self.assertRaises(m.ValidationError):self.sleep([self.sample(stage)])
 def test_numeric_enum(self):self.assertEqual(m.sleep_stage(4),'asleep_deep')
 def test_noon_split(self):
  self.sleep([self.sample(a='2026-10-28T11:30:00-04:00',b='2026-10-28T12:30:00-04:00')]);self.assertEqual(m._load_history('2026-10-29')['sleep']['asleep_seconds'],1800)
 def test_dst(self):
  self.sleep([self.sample(a='2026-11-01T01:00:00-04:00',b='2026-11-01T02:00:00-05:00')]);self.assertEqual(m._load_history('2026-11-01')['sleep']['asleep_seconds'],7200)
 def test_cumulative(self):
  rows=[self.metric(),self.metric(value=200,a='2026-10-28T02:00:00-04:00',b='2026-10-28T03:00:00-04:00')];self.assertEqual(ns['_metric_summary'](rows)['value'],300)
 def test_source_dedupe(self):
  rows=[self.metric(source='watch'),self.metric(source='phone')];self.assertEqual(ns['_metric_summary'](rows)['value'],100)
 def test_daily_total(self):
  m.legacy_steps({'date':'2026-10-28','steps':1000});rows=m._load_history('2026-10-28')['metrics']+[self.metric()];self.assertEqual(ns['_metric_summary'](rows)['value'],1000)
 def test_measurement_latest(self):
  rows=[self.metric(a='2026-10-28T01:00:00-04:00',updated_at=999),self.metric(value=200,a='2026-10-28T03:00:00-04:00',b='2026-10-28T04:00:00-04:00',updated_at=1)];self.assertEqual(ns['_latest_metric'](rows)['value'],200)
 def test_workout_only(self):
  r=m.merge_metrics({'date':'2026-10-28','workouts':[dict(activity='running',start='2026-10-28T01:00:00-04:00',end='2026-10-28T02:00:00-04:00')]});self.assertEqual(r['workouts'],1)
 def test_cross_date_metric(self):
  with self.assertRaises(m.ValidationError):m.merge_metrics({'date':'2026-10-29','metrics':[self.metric()]})
 def test_units(self):
  row=m.normalize_metric(dict(metric='distance',unit='mi',value=1),'2026-10-28');self.assertAlmostEqual(row['value'],1.609344);self.assertEqual(row['unit'],'km')
 def test_negative(self):
  with self.assertRaises(m.ValidationError):m.normalize_metric(dict(metric='active_energy',unit='kcal',value=-1),'2026-10-28')
 def test_missing_value(self):
  with self.assertRaises(m.ValidationError):m.normalize_metric(dict(metric='hrv'),'2026-10-28')
 def test_legacy_backfill(self):
  m.legacy_steps({'date':'2026-10-28','steps':1000});m.legacy_steps({'date':'2026-10-27','steps':100});self.assertEqual(json.loads(m.LEGACY_FILE.read_text())['date'],'2026-10-28')
 def test_section_fallback(self):
  self.sleep([self.sample()]);m.legacy_steps({'date':'2026-10-29','steps':100});self.assertEqual(ns['_load_section']('sleep')['date'],'2026-10-28')
 def test_legacy_snapshot_cumulative(self):
  rows=[self.metric(),self.metric(value=200,a='2026-10-28T02:00:00-04:00',b='2026-10-28T03:00:00-04:00')]
  m.merge_metrics({'date':'2026-10-28','metrics':rows});self.assertEqual(json.loads(m.LEGACY_FILE.read_text())['steps']['steps'],300)
 def test_overlapping_cumulative(self):
  rows=[self.metric(),self.metric(value=200,a='2026-10-28T01:30:00-04:00',b='2026-10-28T03:00:00-04:00')]
  self.assertIsNone(ns['_metric_summary'](rows)['value'])
 def test_http_concurrent_and_auth(self):
  m.TOKEN_FILE.write_text('test-only');srv=m.ThreadingHTTPServer(('127.0.0.1',0),m.Handler);thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start();url=f'http://127.0.0.1:{srv.server_port}/health/ingest'
  def send(i,auth='Bearer test-only'):
   data={'type':'metrics','date':'2026-10-28','metrics':[self.metric(value=i,a=f'2026-10-28T{i:02d}:00:00-04:00',b=f'2026-10-28T{i:02d}:01:00-04:00')]};req=urllib.request.Request(url,data=json.dumps(data).encode(),headers={'Authorization':auth,'Content-Type':'application/json'})
   with urllib.request.urlopen(req) as r:return r.status
  try:
   with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(list(pool.map(send,range(20))),[200]*20)
   self.assertEqual(len(m._load_history('2026-10-28')['metrics']),20)
   with self.assertRaises(urllib.error.HTTPError) as err:send(1,'bad')
   self.assertEqual(err.exception.code,401)
  finally:srv.shutdown();srv.server_close();thread.join()
if __name__=='__main__':unittest.main(verbosity=2)
