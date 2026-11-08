"""Backup service integration in an isolated app/database without credentials."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class BackupRouteTests(unittest.TestCase):
    def test_create_verify_download_and_settings(self):
        code=r'''
import os
os.environ.update(DATABASE_URL='sqlite:///./test.db',AI_PROVIDER='mock',DEBUG='false',BACKUP_ENABLED='false',BACKUP_LOCAL_PATH='./backups')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.database import Base,engine,SessionLocal
from app.models.note import Note
from app.api.backups import router
Base.metadata.create_all(engine)
with SessionLocal() as db:
 db.add(Note(title='fixture',content='public sample'));db.commit()
app=FastAPI();app.include_router(router,prefix='/api')
with TestClient(app) as c:
 assert c.patch('/api/backups/settings',json={'enabled':False,'hour':2,'keep':7}).status_code==200
 assert c.patch('/api/backups/settings',json={'enabled':True,'hour':25,'keep':1}).status_code==422
 r=c.post('/api/backups/');assert r.status_code==202,r.text
 status=c.get('/api/backups/status').json();assert not status['running'] and not status['last_error'],status
 items=c.get('/api/backups/').json();assert len(items)==1,items
 item=items[0];assert item['verified'] and item['notes_count']==1
 assert c.get('/api/backups/'+item['id']+'/download').status_code==200
 r=c.post('/api/backups/'+item['id']+'/verify');assert r.status_code==200,r.text
 assert r.json()['verified']
 with SessionLocal() as db:assert db.query(Note).count()==1
'''
        with tempfile.TemporaryDirectory() as cwd:
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            result=subprocess.run([sys.executable,'-B','-c',code],cwd=cwd,env=env,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
