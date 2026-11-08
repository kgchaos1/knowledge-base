"""Isolated operations API/security tests; no production DB or network calls."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class OperationsTests(unittest.TestCase):
    def test_operations_under_global_auth_and_temporary_restore(self):
        code = r'''
import hashlib, json, os, sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
os.environ.update(DATABASE_URL='sqlite:///./live.db', AI_PROVIDER='mock', DEBUG='false', BACKUP_ENABLED='false', BACKUP_LOCAL_PATH='./backups')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.operations import router
from app.core import backup_service as service, operations_service as operations
from app.core.auth import install_auth, hash_password
from app.core.backup_archive import create_archive
from app.core.database import Base, engine, SessionLocal
from app.models.note import Note, ImportTask, DataSource
Base.metadata.create_all(engine)
images=Path('images');images.mkdir()
payload=b'fixture image';name=hashlib.sha256(payload).hexdigest()+'.png'
(images/name).write_bytes(payload)
with SessionLocal() as db:
 db.add(Note(title='archived',content='archive only',images=[{'path':name},{'path':name}]))
 db.commit()
item=create_archive(Path('live.db'), images, service.ROOT)
identifier='a'*32
service.ROOT.mkdir(parents=True,exist_ok=True)
(service.ROOT/(identifier+'.metadata.json')).write_text(json.dumps({'id':identifier,'filename':item['filename'],'created_at':'2026-01-01'}))
# Post-backup live changes prove the rehearsal restores the archived DB, not live.
with SessionLocal() as db:
 db.add(Note(title='live',content='must not change',images=[{'url':'https://invalid.test/img'}, {'path':'../secret'}, {'path':name.upper()}, {'path':name}]))
 db.add(Note(title='malformed',content='no image references',images={'path':name}))
 db.add(Note(title='mixed',content='no image references',images=[None,42,'not a record',{'path':17},{'path':'../'+name}]))
 db.add(ImportTask(platform='zhihu',status='failed',config={'Cookie':'SECRET_SENTINEL'},error_message='SECRET_SENTINEL'))
 db.add(DataSource(name='active',collection_id='1',collection_url='https://invalid.test/1',sync_enabled=True,active_task_id=17))
 db.add(DataSource(name='off',collection_id='2',collection_url='https://invalid.test/2',sync_enabled=False))
 db.commit()
original=Path('live.db').read_bytes()
service.STATE.update(running=False,last_error='C:/SECRET_SENTINEL/cookie')
app=FastAPI()
config=SimpleNamespace(PKM_PUBLIC_ORIGIN='https://pkm.test',PKM_SESSION_TTL_SECONDS=3600,PKM_LOGIN_MAX_ATTEMPTS=20,PKM_LOGIN_WINDOW_SECONDS=60,SECRET_KEY='S'*40,PKM_TRUSTED_PROXY_PEERS='',PKM_PASSWORD_HASH=hash_password('fixture-password'))
install_auth(app,config)
app.include_router(router,prefix='/api')
with TestClient(app,base_url='https://pkm.test') as client:
 url='/api/operations/backups/'+identifier+'/rehearse'
 assert client.get('/api/operations/status').status_code==401
 assert client.post(url,headers={'Origin':'https://pkm.test'}).status_code==401
 assert client.post('/api/auth/login',headers={'Origin':'https://pkm.test'},json={'password':'fixture-password'}).status_code==200
 assert client.get('/api/operations/status',headers={'Authorization':'Bearer device'}).status_code==403
 assert client.post(url).status_code==403
 assert client.post(url,headers={'Origin':'https://other.test'}).status_code==403
 # GET must never verify/hash archives, list files, or access the network.
 with patch.object(operations,'verify_archive',side_effect=AssertionError('GET hash forbidden')), patch.object(Path,'rglob',side_effect=AssertionError('scan forbidden')):
  response=client.get('/api/operations/status')
 assert response.status_code==200,response.text
 assert response.headers['cache-control']=='no-store'
 snapshot=response.json()
 assert snapshot['notes_count']==4 and snapshot['failed_imports_count']==1,snapshot
 assert snapshot['attachments']=={'references_count':3,'unique_count':1}
 assert snapshot['source_scheduler']=={'enabled_count':1,'active_count':1}
 assert set(snapshot['disk'])=={'total','free','used'}
 assert all(type(v) is int and v>=0 for v in snapshot['disk'].values())
 assert snapshot['backup']=={'running':False,'last_error':'Backup did not complete'}
 assert 'SECRET_SENTINEL' not in response.text and str(Path.cwd()) not in response.text
 headers={'Origin':'https://pkm.test'}
 response=client.post(url,headers=headers)
 assert response.status_code==200,response.text
 assert response.json()=={'verified':True,'notes_count':1,'files_count':2,'sqlite_sample_read':True,'sqlite_restore_verified':True,'live_data_modified':False}
 assert str(Path.cwd()) not in response.text
 assert Path('live.db').read_bytes()==original
 with SessionLocal() as db: assert db.query(Note).count()==4
 with patch.object(service,'lookup',side_effect=AssertionError('bad id reached inventory')):
  for bad in ['not-a-uuid','a'*31,'G'*32,'https:evil']:
   response=client.post('/api/operations/backups/'+bad+'/rehearse',headers=headers)
   assert response.status_code==404,response.text
 response=client.post('/api/operations/backups/'+'b'*32+'/rehearse',headers=headers)
 assert response.status_code==404 and response.json()['detail']['code']=='backup_not_found'
 # Corrupt archive fails closed with fixed structured errors, never verifier paths.
 archive=service.ROOT/item['filename'];archive.write_bytes(b'broken SECRET_SENTINEL')
 response=client.post(url,headers=headers)
 assert response.status_code==422,response.text
 assert response.json()['detail']['code']=='rehearsal_failed'
 assert 'SECRET_SENTINEL' not in response.text and str(Path.cwd()) not in response.text
 assert Path('live.db').read_bytes()==original
 # Polluted metadata cannot read any other local path.
 with patch.object(service,'lookup',return_value={'filename':'../live.db'}):
  assert client.post(url,headers=headers).status_code==422
 with patch.object(service,'lookup',side_effect=OSError('SECRET_SENTINEL')):
  response=client.post(url,headers=headers)
  assert response.status_code==503 and 'SECRET_SENTINEL' not in response.text
 with patch.object(operations,'status_snapshot',side_effect=OSError('SECRET_SENTINEL')):
  response=client.get('/api/operations/status')
  assert response.status_code==503 and response.json()['detail']['code']=='operations_unavailable'
  assert 'SECRET_SENTINEL' not in response.text
engine.dispose()
'''
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            result = subprocess.run([sys.executable, '-B', '-c', code], cwd=directory,
                                    env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
