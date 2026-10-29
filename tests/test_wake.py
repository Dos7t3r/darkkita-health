import ast,asyncio,importlib.util,json,os,pathlib,tempfile,unittest
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo
P=pathlib.Path(__file__).resolve().parents[1]
os.environ['HEALTH_TIMEZONE']='America/Toronto'
spec=importlib.util.spec_from_file_location('ingest',P/'server'/'health_ingest.py');ingest=importlib.util.module_from_spec(spec);spec.loader.exec_module(ingest)
tree=ast.parse((P/'main.py').read_text())
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AppleHealthPlugin')
ns={'datetime':datetime,'timedelta':timedelta,'os':os,'json':json,'TZ':ZoneInfo('America/Toronto'),'logger':SimpleNamespace(info=lambda *x:None)}
code=ast.Module(body=[ast.ClassDef(name='Worker',bases=[],keywords=[],decorator_list=[],body=[n for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in {'_process_wake_events','_save_wake_event','_wake_prompt'}])],type_ignores=[])
exec(compile(ast.fix_missing_locations(code),'worker','exec'),ns)
class Manager:
 def __init__(self):self.jobs=[SimpleNamespace(job_id='old-morning',enabled=True,name='Darkkita晨间播报')];self.created=[];self.fail=False
 async def list_jobs(self):return list(self.jobs)
 async def update_job(self,job_id,**kw):
  if self.fail:raise RuntimeError('test scheduler unavailable')
  job=next(j for j in self.jobs if j.job_id==job_id)
  for k,v in kw.items():setattr(job,k,v)
  return job
 async def add_active_job(self,**kw):
  j=SimpleNamespace(job_id='new-'+str(len(self.created)),**kw);self.jobs.append(j);self.created.append(j);return j
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);ingest.DATA_ROOT=self.root;ingest.HISTORY_DIR=self.root/'history';ingest.HISTORY_DIR.mkdir();(ingest.HISTORY_DIR/'2026-10-28.json').write_text('{}')
  ns.update(DATA_ROOT=self.root,_read_json=lambda p,d:json.loads(p.read_text()) if p.exists() else d,
            _load_date=lambda day:{},format_health=lambda *x:'summary')
  self.manager=Manager();self.worker=ns['Worker']();self.worker.context=SimpleNamespace(cron_manager=self.manager)
  self.worker._wake_settings=lambda:{'enabled':True,'session':'test:FriendMessage:test','replaces_job_id':'old-morning'}
 def tearDown(self):self.tmp.cleanup()
 def event(self,**kw):
  p={'occurred_at':datetime.now(timezone.utc).isoformat(),'holiday_mode':True};p.update(kw);return ingest.accept_wake_event(p)
 def process(self):asyncio.run(self.worker._process_wake_events())
 def file(self):return next((self.root/'wake_events').glob('*.json'))
 def test_event_dedup(self):
  self.assertTrue(self.event()['queued']);self.assertTrue(self.event()['duplicate']);self.assertEqual(len(list((self.root/'wake_events').glob('*.json'))),1)
 def test_stale_rejected(self):
  with self.assertRaises(ingest.ValidationError):self.event(occurred_at=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat())
 def test_holiday_type(self):
  with self.assertRaises(ingest.ValidationError):self.event(holiday_mode='false')
 def test_upload_required(self):
  for p in ingest.HISTORY_DIR.glob('*'):p.unlink()
  with self.assertRaises(ingest.ValidationError):self.event()
 def test_missing_time(self):
  with self.assertRaises(ingest.ValidationError):self.event(occurred_at=None)
 def test_worker_schedules_once_and_disables_fixed(self):
  self.event();self.process();self.process();self.assertEqual(len(self.manager.created),1)
  self.assertFalse(self.manager.jobs[0].enabled);j=self.manager.created[0];self.assertTrue(j.enabled);self.assertTrue(j.run_once)
  self.assertGreater((j.run_at-datetime.now(ns['TZ'])).total_seconds(),170)
  self.assertIn('Reading Week',j.payload['note']);self.assertIn('不罗列数值',j.payload['note'])
 def test_staged_missing_not_resent(self):
  self.event();p=self.file();d=json.loads(p.read_text());d.update(status='staged',job_id='already-consumed');p.write_text(json.dumps(d));self.process();self.assertFalse(self.manager.created);self.assertEqual(json.loads(p.read_text())['status'],'consumed')
 def test_staged_existing_reenabled(self):
  self.event();self.process();p=self.file();d=json.loads(p.read_text());d['status']='staged';p.write_text(json.dumps(d));self.manager.created[0].enabled=False;self.process();self.assertTrue(self.manager.created[0].enabled);self.assertEqual(len(self.manager.created),1)
 def test_expired_not_scheduled(self):
  self.event();p=self.file();d=json.loads(p.read_text());d['occurred_at']=(datetime.now(timezone.utc)-timedelta(hours=3)).isoformat();p.write_text(json.dumps(d));self.process();self.assertFalse(self.manager.created)
 def test_regular_upload_no_event(self):
  self.process();self.assertFalse(self.manager.created)
 def test_disabled_no_touch(self):
  self.event();self.worker._wake_settings=lambda:{'enabled':False};self.process();self.assertTrue(self.manager.jobs[0].enabled);self.assertFalse(self.manager.created)
 def test_ordinary_day_no_holiday_assumption(self):
  self.event(holiday_mode=False);self.process();self.assertNotIn('Reading Week',self.manager.created[0].payload['note'])
if __name__=='__main__':unittest.main(verbosity=2)
