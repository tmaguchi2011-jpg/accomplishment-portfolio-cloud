"""Supabase REST persistence. No credentials or data are stored in the repository."""
import io
import json
import math
import re
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

TABLES = {
    'projects': ['id','title','created_on','category','work_type','status','description','problem','outcome','impact','audience','collaborators','tools','tags','hours','updated_at'],
    'deliverables': ['project_id','kind'],
    'links': ['id','project_id','label','url'],
    'files': ['id','project_id','original_name','saved_name','size_bytes'],
    'activities': ['id','project_id','activity_on','note','hours'],
}
PROJECT_FIELDS = TABLES['projects'][1:-1]
MAX_ARCHIVE = 200 * 1024 * 1024
MAX_EXPANDED = 400 * 1024 * 1024
MAX_DATABASE = 50 * 1024 * 1024
MAX_ROWS = 100000

class CloudError(Exception):
    """Safe user-facing error; excludes credentials, response bodies and URLs."""

class Api:
    def __init__(self, url, key, bearer=None):
        if not isinstance(url,str) or not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::443)?/?',url):
            raise CloudError('Set a valid HTTPS Supabase project URL in Secrets.')
        self.url=url.rstrip('/');self.key=key;self.bearer=bearer
        if not key:raise CloudError('A required Supabase API key is missing in Secrets.')
    def request(self, method, path, data=None, raw=False, headers=None):
        h={'apikey':self.key}
        if self.bearer:h['Authorization']='Bearer '+self.bearer
        if data is not None and not isinstance(data,bytes):
            data=json.dumps(data,allow_nan=False).encode();h['Content-Type']='application/json'
        h.update(headers or {})
        req=Request(self.url+path,data=data,headers=h,method=method)
        try:
            with urlopen(req,timeout=90) as response:
                body=response.read()
                return body if raw else (json.loads(body) if body else None)
        except HTTPError as exc:
            status=exc.code;exc.close()
            if status in (401,403):raise CloudError('Access denied. Check the account, API keys and database setup.') from None
            if status==413:raise CloudError('Upload exceeds the configured storage limit.') from None
            if status==409:raise CloudError('This record or storage object already exists. Refresh before trying again.') from None
            if status==404:raise CloudError('Required cloud table, function or file was not found. Check setup.sql and the bucket name.') from None
            raise CloudError(f'Cloud operation failed (HTTP {status}). Check Supabase project status and setup.') from None
        except (URLError,TimeoutError,OSError):
            raise CloudError('Cannot reach cloud storage. Check your connection and Supabase project status.') from None

class Store:
    def __init__(self, url, service_key, bucket='TomosAccomplishments', max_file_mb=50):
        # A legacy service_role JWT is used server-side, never exposed to the browser.
        if not isinstance(service_key,str) or not service_key.startswith(('eyJ','sb_secret_')):
            raise CloudError('Use a Supabase secret API key (or legacy service_role key) in server Secrets.')
        self.api=Api(url,service_key,service_key if service_key.startswith('eyJ') else None);self.bucket=bucket
        self.max_file_bytes=int(max_file_mb)*1024*1024
        if not bucket or self.max_file_bytes<=0:raise CloudError('Check bucket and max_file_mb settings.')
    def select(self, table, columns='*', filters=None, order=None):
        if table not in TABLES:raise ValueError('Unknown table')
        q={'select':columns}
        q.update({key:'eq.'+str(value) for key,value in (filters or {}).items()})
        q['order']=order or ('project_id.asc,kind.asc' if table=='deliverables' else 'id.asc')
        result=[];offset=0
        # Range paging prevents the default API row limit from truncating exports.
        while True:
            page=self.api.request('GET','/rest/v1/portfolio_'+table+'?'+urlencode(q),headers={'Range-Unit':'items','Range':f'{offset}-{offset+499}'})
            if not page:break
            result.extend(page);offset+=len(page)
            if offset>MAX_ROWS:raise CloudError('Portfolio exceeds the current export row limit.')
            if len(page)<500:break
        return result
    def rows(self,sql,params=()):
        # Compatibility for the existing UI, restricted to its known SELECT syntax.
        m=re.fullmatch(r'SELECT (\*|[a-z_, ]+) FROM (projects|deliverables|links|files|activities)(?: WHERE (id|project_id)=\?)?(?: ORDER BY ([a-zA-Z_, ]+))?',sql)
        if not m:raise ValueError('Unsupported query')
        columns,table,key,order=m.groups()
        filters={key:params[0]} if key else {}
        if bool(key)!=bool(params):raise ValueError('Invalid query parameters')
        ordering=','.join(part.strip().split()[0]+('.desc' if part.strip().endswith(' DESC') else '.asc') for part in order.split(',')) if order else None
        return self.select(table,columns.replace(' ',''),filters,ordering)
    def rpc(self,name,data):return self.api.request('POST','/rest/v1/rpc/'+name,data)
    def save_project(self,data,kinds,pid=None):
        return self.rpc('portfolio_save_project',{'p_id':pid or uuid.uuid4().hex,'p_data':data,'p_kinds':list(dict.fromkeys(kinds)),'p_create':pid is None})
    def insert(self,table,row):
        return self.api.request('POST','/rest/v1/portfolio_'+table,row,headers={'Prefer':'return=minimal'})
    def delete(self,table,filters):
        q=urlencode({k:'eq.'+str(v) for k,v in filters.items()})
        return self.api.request('DELETE','/rest/v1/portfolio_'+table+'?'+q,headers={'Prefer':'return=minimal'})
    def _object(self,path):return '/storage/v1/object/'+quote(self.bucket,safe='')+'/'+quote(path,safe='/')
    def upload(self,path,content):
        if len(content)>self.max_file_bytes:raise CloudError('A file exceeds the configured per-file storage limit.')
        self.api.request('POST',self._object(path),content,headers={'Content-Type':'application/octet-stream','x-upsert':'false'})
    def download(self,record):return self.api.request('GET',self._object(record['storage_path']),raw=True)
    def remove_objects(self,paths):
        for start in range(0,len(paths),100):
            self.api.request('DELETE','/storage/v1/object/'+quote(self.bucket,safe=''),{'prefixes':paths[start:start+100]})
    def save_files(self,pid,uploads,allowed_types):
        # Validate every file before uploading the first one.
        files=[]
        for item in uploads:
            name=Path(item.name).name;suffix=Path(name).suffix.lower()
            if suffix.lstrip('.') not in allowed_types:raise ValueError('Unsupported file type: '+name)
            if item.size>self.max_file_bytes:raise ValueError('File exceeds storage limit: '+name)
            content=item.getvalue();fid=uuid.uuid4().hex;saved=fid+suffix
            files.append(({'id':fid,'project_id':pid,'original_name':name,'saved_name':saved,'size_bytes':len(content),'storage_path':f'uploads/{pid}/{saved}'},content))
        for record,content in files:
            self.upload(record['storage_path'],content)
            try:self.insert('files',record)
            except Exception:
                try:self.remove_objects([record['storage_path']])
                except CloudError:pass
                raise
        return len(files)
    def delete_record(self,kind,rid,pid):
        if kind not in ('links','files','activities'):raise ValueError('Invalid record type')
        items=self.select(kind,filters={'id':rid,'project_id':pid})
        if not items:return
        self.delete(kind,{'id':rid,'project_id':pid})
        if kind=='files':
            try:self.remove_objects([items[0]['storage_path']])
            except CloudError:raise CloudError('Record removed, but storage cleanup failed. An unused object remains in the bucket.') from None
    def delete_project(self,pid):
        files=self.select('files',filters={'project_id':pid})
        self.delete('projects',{'id':pid})
        try:self.remove_objects([x['storage_path'] for x in files])
        except CloudError:raise CloudError('Project removed, but storage cleanup failed. Unused objects remain in the bucket.') from None
    def backup_bytes(self):
        # One database snapshot via RPC, followed by corresponding object downloads.
        tables=self.rpc('portfolio_snapshot',{})
        data=io.BytesIO()
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'portfolio.db'
            with closing(sqlite3.connect(db)) as con:
                con.executescript(SQLITE_SCHEMA)
                for table,columns in TABLES.items():
                    con.executemany('INSERT INTO '+table+' ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',[[r[k] for k in columns] for r in tables[table]])
                con.commit();dump='\n'.join(con.iterdump())
            with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as z:
                z.write(db,'portfolio.db');z.writestr('portfolio.sql',dump)
                for r in tables['files']:
                    content=self.download(r)
                    if len(content)!=r['size_bytes']:raise CloudError('Attachment size mismatch. Backup was not completed.')
                    z.writestr(f"uploads/{r['project_id']}/{r['saved_name']}",content)
        return data.getvalue()
    def import_backup(self,content):
        plan=inspect_backup(content,self.max_file_bytes)
        prefix='imports/'+uuid.uuid4().hex;uploaded=[]
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                for r in plan['tables']['files']:
                    member=f"uploads/{r['project_id']}/{r['saved_name']}";r['storage_path']=prefix+'/'+member
                    self.upload(r['storage_path'],z.read(member));uploaded.append(r['storage_path'])
        except Exception:
            try:self.remove_objects(uploaded)
            except CloudError:pass
            raise
        # A network failure during commit is ambiguous: retain objects so committed
        # records can never lose their attachments. A retry requires an empty DB.
        try:self.rpc('portfolio_import',{'p_tables':plan['tables']})
        except Exception:
            raise CloudError('Import completion could not be confirmed. Refresh and check project counts before retrying. Uploaded objects were retained to protect any committed records.') from None
        return plan['counts']

SQLITE_SCHEMA='''
CREATE TABLE projects (id TEXT PRIMARY KEY,title TEXT NOT NULL,created_on TEXT NOT NULL,category TEXT NOT NULL,work_type TEXT NOT NULL,status TEXT NOT NULL,description TEXT NOT NULL,problem TEXT NOT NULL,outcome TEXT NOT NULL,impact TEXT NOT NULL,audience TEXT NOT NULL,collaborators TEXT NOT NULL,tools TEXT NOT NULL,tags TEXT NOT NULL,hours REAL NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE deliverables (project_id TEXT NOT NULL REFERENCES projects(id),kind TEXT NOT NULL,PRIMARY KEY(project_id,kind));
CREATE TABLE links (id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),label TEXT NOT NULL,url TEXT NOT NULL);
CREATE TABLE files (id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),original_name TEXT NOT NULL,saved_name TEXT NOT NULL,size_bytes INTEGER NOT NULL);
CREATE TABLE activities (id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),activity_on TEXT NOT NULL,note TEXT NOT NULL,hours REAL NOT NULL);
'''

def inspect_backup(content,max_file_bytes):
    if len(content)>MAX_ARCHIVE:raise ValueError('Backup ZIP exceeds 200 MB. Contact the maintainer for a large-file migration.')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            infos=z.infolist();names=[i.filename for i in infos]
            if len(names)!=len(set(names)):raise ValueError('Backup has duplicate archive entries.')
            if len(infos)>20000 or sum(i.file_size for i in infos)>MAX_EXPANDED:raise ValueError('Backup exceeds the expanded-size or file-count limit.')
            for i in infos:
                name=i.filename;p=PurePosixPath(name)
                if '\\' in name or p.is_absolute() or '..' in p.parts or ':' in name or name!=p.as_posix()+( '/' if i.is_dir() else ''):
                    raise ValueError('Backup contains an unsafe archive path.')
                if (i.external_attr>>16)&0o170000==0o120000:raise ValueError('Backup contains a symbolic link.')
            if 'portfolio.db' not in names:raise ValueError('Backup must contain portfolio.db at the ZIP root.')
            if z.getinfo('portfolio.db').file_size>MAX_DATABASE:raise ValueError('Backup database exceeds 50 MB.')
            tables={}
            with tempfile.TemporaryDirectory() as tmp:
                db=Path(tmp)/'portfolio.db';db.write_bytes(z.read('portfolio.db'))
                with closing(sqlite3.connect(db.as_uri()+'?mode=ro&immutable=1',uri=True)) as con:
                    con.row_factory=sqlite3.Row;con.execute('PRAGMA trusted_schema=OFF');con.execute('PRAGMA query_only=ON')
                    if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup database failed its integrity check.')
                    for table,cols in TABLES.items():
                        entry=con.execute('SELECT type,sql FROM sqlite_master WHERE name=?',(table,)).fetchone()
                        if not entry or entry['type']!='table' or not entry['sql'].lstrip().upper().startswith('CREATE TABLE'):
                            raise ValueError('Backup has an unsupported database schema.')
                        records=con.execute('SELECT '+','.join(cols)+' FROM '+table+' LIMIT '+str(MAX_ROWS+1)).fetchall()
                        if len(records)>MAX_ROWS:raise ValueError('Backup exceeds the row limit.')
                        tables[table]=[dict(r) for r in records]
            ids=set()
            for r in tables['projects']:
                validate_id(r['id'])
                if r['id'] in ids:raise ValueError('Duplicate project ID')
                ids.add(r['id']);date.fromisoformat(r['created_on']);datetime.fromisoformat(r['updated_at'])
                if not r['title'].strip():raise ValueError('A project title is empty.')
            unique={t:set() for t in TABLES}
            for table,records in tables.items():
                for r in records:
                    for key,value in r.items():
                        if key in ('hours','size_bytes'):
                            if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ValueError('Invalid numeric field in backup.')
                        elif not isinstance(value,str):raise ValueError('Invalid text field in backup.')
                    if table!='projects' and r['project_id'] not in ids:raise ValueError('An attachment or record refers to a missing project.')
                    if 'id' in r:
                        validate_id(r['id']);pk=r['id']
                    else:pk=(r['project_id'],r['kind'])
                    if pk in unique[table]:raise ValueError('Duplicate record key')
                    unique[table].add(pk)
                    if table=='activities':date.fromisoformat(r['activity_on'])
                    if table=='links' and not re.fullmatch(r'https?://[^\s]+',r['url'],re.I):raise ValueError('A saved link has an unsupported URL.')
            required=set()
            for r in tables['files']:
                if not re.fullmatch(r'[A-Za-z0-9_.-]+',r['saved_name']) or r['saved_name'] in ('.','..'):raise ValueError('Invalid saved file name.')
                member=f"uploads/{r['project_id']}/{r['saved_name']}"
                if member in required:raise ValueError('Duplicate attachment path.')
                required.add(member)
                if member not in names:raise ValueError('Backup is missing attachment: '+r['original_name'])
                size=z.getinfo(member).file_size
                if size!=r['size_bytes']:raise ValueError('Attachment size differs from its database record.')
                if size>max_file_bytes:raise ValueError('Attachment exceeds bucket file limit: '+r['original_name'])
            # Never silently discard unregistered evidence from a backup.
            extras={i.filename for i in infos if not i.is_dir() and i.filename not in ('portfolio.db','portfolio.sql')}-required
            if extras:raise ValueError('Backup contains files not registered in the database. Resolve these before migration: '+', '.join(sorted(extras)[:3]))
            return {'tables':tables,'counts':{t:len(r) for t,r in tables.items()},'file_bytes':sum(r['size_bytes'] for r in tables['files'])}
    except (zipfile.BadZipFile,sqlite3.Error,KeyError,TypeError,OverflowError) as exc:
        raise ValueError('Backup ZIP or database is invalid or incompatible.') from None

def validate_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value):raise ValueError('Invalid record ID in backup.')
