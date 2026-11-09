import ast
import copy
import io
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError,URLError
from urllib.parse import parse_qs,urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cloud_store import Api,Store,CloudError,TABLES,SQLITE_SCHEMA,inspect_backup

def fixture():
 return {
 'projects':[dict(id='project1',title='Linablue',created_on='2026-11-08',category='Python Application',work_type='simulation',status='Implemented',description='Overview',problem='Scheduling',outcome='Prototype',impact='Planning',audience='EEC',collaborators='Team',tools='Python',tags='UF1',hours=8.,updated_at='2026-11-08T10:00:00')],
 'deliverables':[dict(project_id='project1',kind='Python App')],
 'links':[dict(id='link1',project_id='project1',label='Gamma',url='https://example.com/deck')],
 'files':[dict(id='file1',project_id='project1',original_name='evidence.txt',saved_name='file1.txt',size_bytes=8)],
 'activities':[dict(id='activity1',project_id='project1',activity_on='2026-11-08',note='Deployment',hours=1.)]}

def archive(tables=None,missing=False,extra=None):
 tables=copy.deepcopy(tables if tables is not None else fixture());data=io.BytesIO()
 with tempfile.TemporaryDirectory() as t:
  p=Path(t)/'portfolio.db'
  with closing(sqlite3.connect(p)) as c:
   c.executescript(SQLITE_SCHEMA)
   for table,cols in TABLES.items():
    c.executemany('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in cols)+')',[[r[k] for k in cols] for r in tables[table]])
   c.commit()
  with zipfile.ZipFile(data,'w') as z:
   z.write(p,'portfolio.db');z.writestr('portfolio.sql','-- Not executed during import')
   if not missing:
    for r in tables['files']:z.writestr(f"uploads/{r['project_id']}/{r['saved_name']}",b'evidence')
   if extra:z.writestr(extra,b'bad')
 return data.getvalue()

class FakeApi:
 def __init__(self,tables=None,objects=None):
  self.tables=copy.deepcopy(tables if tables is not None else {t:[] for t in TABLES});self.objects=dict(objects or {});self.calls=[];self.fail_upload=False;self.fail_commit=False
 def request(self,method,path,data=None,raw=False,headers=None):
  self.calls.append((method,path,data,headers))
  if path.startswith('/rest/v1/rpc/portfolio_snapshot'):return copy.deepcopy(self.tables)
  if path.startswith('/rest/v1/rpc/portfolio_import'):
   if self.fail_commit:raise CloudError('injected ambiguous timeout')
   if self.tables['projects']:raise CloudError('not empty')
   self.tables=copy.deepcopy(data['p_tables']);return None
  if path.startswith('/storage/v1/object/'):
   from urllib.parse import unquote
   tail=unquote(path.split('/storage/v1/object/',1)[1]);parts=tail.split('/',1)
   if method=='DELETE':
    for k in data['prefixes']:self.objects.pop(k,None)
    return None
   key=parts[1]
   if method=='POST':
    if self.fail_upload:raise CloudError('injected upload failure')
    self.objects[key]=data;return {}
   return self.objects[key]
  if path.startswith('/rest/v1/portfolio_'):
   url=urlsplit(path);table=url.path.split('portfolio_',1)[1];q=parse_qs(url.query)
   if method=='POST':self.tables[table].append(copy.deepcopy(data));return
   if method=='DELETE':
    self.tables[table]=[r for r in self.tables[table] if not all(str(r[k])==v[0][3:] for k,v in q.items())];return
   rs=copy.deepcopy(self.tables[table])
   for k,v in q.items():
    if k not in ('select','order'):rs=[r for r in rs if str(r[k])==v[0][3:]]
   a,b=map(int,headers['Range'].split('-'));rs=rs[a:b+1]
   columns=q['select'][0]
   return rs if columns=='*' else [{k:r[k] for k in columns.split(',')} for r in rs]
  raise AssertionError((method,path))

def store(fake=None):
 s=Store('https://example.supabase.co','sb_secret_test');s.api=fake or FakeApi();return s

class BackupTests(unittest.TestCase):
 def test_original_format_preview(self):
  plan=inspect_backup(archive(),50*1024*1024)
  self.assertEqual(plan['tables'],fixture());self.assertEqual(plan['file_bytes'],8)
 def test_missing_attachment(self):
  with self.assertRaisesRegex(ValueError,'missing attachment'):inspect_backup(archive(missing=True),100)
 def test_unsafe_path(self):
  for name in ['../stolen.txt','/root.txt','bad\\name.txt','C:/windows.txt']:
   with self.subTest(name=name),self.assertRaises(ValueError):inspect_backup(archive(extra=name),100)
 def test_unregistered_file(self):
  with self.assertRaisesRegex(ValueError,'not registered'):inspect_backup(archive(extra='uploads/orphan.txt'),100)
 def test_size_and_bucket_limits(self):
  with self.assertRaisesRegex(ValueError,'bucket file limit'):inspect_backup(archive(),4)
  r=fixture();r['files'][0]['size_bytes']=9
  with self.assertRaisesRegex(ValueError,'size differs'):inspect_backup(archive(r),100)
 def test_invalid_database(self):
  data=io.BytesIO()
  with zipfile.ZipFile(data,'w') as z:z.writestr('portfolio.db',b'not a database')
  with self.assertRaises(ValueError):inspect_backup(data.getvalue(),100)
 def test_orphan_record(self):
  r=fixture();r['links'][0]['project_id']='missing'
  with self.assertRaisesRegex(ValueError,'missing project'):inspect_backup(archive(r),100)
 def test_link_scheme(self):
  r=fixture();r['links'][0]['url']='javascript:alert(1)'
  with self.assertRaisesRegex(ValueError,'unsupported URL'):inspect_backup(archive(r),100)
 def test_import_preserves_records_and_backup_roundtrip(self):
  f=FakeApi();s=store(f);counts=s.import_backup(archive())
  self.assertEqual(counts,{t:len(v) for t,v in fixture().items()})
  for t in TABLES:
   self.assertEqual([{k:r[k] for k in TABLES[t]} for r in f.tables[t]],fixture()[t])
  self.assertEqual(list(f.objects.values()),[b'evidence'])
  again=inspect_backup(s.backup_bytes(),100)
  self.assertEqual(again['tables'],fixture())
 def test_import_preflight_no_mutation(self):
  f=FakeApi();s=store(f)
  with self.assertRaises(ValueError):s.import_backup(archive(missing=True))
  self.assertEqual(f.calls,[])
 def test_uncertain_commit_keeps_uploaded_objects(self):
  f=FakeApi();f.fail_commit=True;s=store(f)
  with self.assertRaisesRegex(CloudError,'could not be confirmed'):s.import_backup(archive())
  self.assertEqual(len(f.objects),1)
 def test_upload_failure_prevents_import(self):
  f=FakeApi();f.fail_upload=True;s=store(f)
  with self.assertRaises(CloudError):s.import_backup(archive())
  self.assertFalse(any('portfolio_import' in c[1] for c in f.calls))
 def test_failed_second_upload_cleans_first(self):
  r=fixture();r['files'].append(dict(r['files'][0],id='file2',saved_name='file2.txt'))
  f=FakeApi();s=store(f);real_upload=s.upload;count=[0]
  def upload(path,content):
   count[0]+=1
   if count[0]==2:raise CloudError('Second upload failed')
   real_upload(path,content)
  s.upload=upload
  with self.assertRaises(CloudError):s.import_backup(archive(r))
  self.assertEqual(f.objects,{})
 def test_metadata_failure_cleans_new_upload(self):
  f=FakeApi();s=store(f)
  class Item:
   name='new.txt';size=8
   def getvalue(self):return b'evidence'
  def fail_insert(*args):raise CloudError('Metadata rejected')
  s.insert=fail_insert
  with self.assertRaises(CloudError):s.save_files('project1',[Item()],['txt'])
  self.assertEqual(f.objects,{})
 def test_rows_paging_and_order(self):
  r=fixture();r['activities']=[dict(r['activities'][0],id=str(i)) for i in range(1207)]
  f=FakeApi(r);s=store(f)
  self.assertEqual(len(s.rows('SELECT * FROM activities WHERE project_id=? ORDER BY activity_on DESC',('project1',))),1207)
  self.assertEqual(len(f.calls),3)
  self.assertIn('activity_on.desc',f.calls[0][1])
  s.rows('SELECT * FROM projects ORDER BY created_on DESC, title')
  self.assertIn('created_on.desc',f.calls[-1][1])
 def test_project_save_rpc(self):
  s=store();s.rpc=lambda name,data:(name,data)
  name,p=s.save_project({'title':'A'},['Python App','Python App'],'p1')
  self.assertEqual(name,'portfolio_save_project');self.assertFalse(p['p_create']);self.assertEqual(p['p_kinds'],['Python App'])
 def test_requests_keep_secret_server_side_and_do_not_make_it_a_jwt(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self):return b'[]'
  with patch('cloud_store.urlopen',return_value=Response()) as call:
   s=Store('https://example.supabase.co','sb_secret_test');s.select('projects')
   req=call.call_args.args[0]
   self.assertEqual(req.get_header('Apikey'),'sb_secret_test');self.assertIsNone(req.get_header('Authorization'))
 def test_sanitized_transport_error(self):
  with patch('cloud_store.urlopen',side_effect=URLError('sb_secret_DO_NOT_LEAK')):
   with self.assertRaises(CloudError) as error:store().api=Api('https://example.supabase.co','sb_secret_test');Api('https://example.supabase.co','sb_secret_test').request('GET','/rest/v1/foo')
   self.assertNotIn('DO_NOT_LEAK',str(error.exception))
 def test_schema_permissions_and_gate_before_store(self):
  root=Path(__file__).resolve().parents[1];sql=(root/'setup.sql').read_text();source=(root/'app.py').read_text()
  for t in TABLES:self.assertIn('ALTER TABLE public.portfolio_'+t+' ENABLE ROW LEVEL SECURITY',sql)
  self.assertIn('REVOKE ALL ON FUNCTION public.portfolio_import(jsonb) FROM PUBLIC,anon,authenticated',sql)
  self.assertLess(source.index('require_login(settings)'),source.index('store = Store'))
  self.assertNotIn('UPLOAD_DIR',source)
  ast.parse(source);ast.parse((root/'auth_ui.py').read_text())

if __name__=='__main__':unittest.main()
