"""Real app integration in an isolated subprocess, with no external requests."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AuthenticatedFlowTests(unittest.TestCase):
    def test_real_app_login_source_sync_and_note_retention(self):
        backend = str(Path(__file__).resolve().parents[1])
        code = r'''
import os
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.core.auth import hash_password
os.environ.update(PKM_PUBLIC_ORIGIN='https://pkm.test', SECRET_KEY='x'*48,
    PKM_PASSWORD_HASH=hash_password('isolated-test-password'), AI_PROVIDER='mock',
    DATABASE_URL='sqlite:///./isolated.db', DEBUG='false', ZHIHU_COOKIE='offline-test-placeholder')
from app.main import app
from app.models.note import Note
async def fake_import(config, db):
    assert config['collection_url']=='https://www.zhihu.com/collection/123'
    db.add(Note(title='Isolated example', content='A fixed test note.', source='zhihu',
                source_id='answer:123',source_url='https://www.zhihu.com/question/1/answer/123'))
    db.commit()
    return dict(total=1,success=1,failed=0,status='completed')
with patch('app.importers.zhihu.import_from_zhihu', fake_import), \
     patch('httpx.AsyncClient.request', side_effect=AssertionError('No network allowed')), \
     TestClient(app,base_url='https://pkm.test') as c:
    assert c.get('/api/notes/').status_code==401
    assert c.get('/docs').status_code==401
    headers={'Origin':'https://pkm.test'}
    r=c.post('/api/auth/login',json={'password':'isolated-test-password'},headers=headers)
    assert r.status_code==200,r.text
    assert c.post('/api/sources/',json={'collection_url':'123'}).status_code==403
    r=c.post('/api/sources/',json={'name':'Test collection','collection_url':'https://www.zhihu.com/collection/123/?utm=example'},headers=headers)
    assert r.status_code==201,r.text
    source=r.json()
    r=c.post('/api/sources/%s/sync'%source['id'],headers=headers)
    assert r.status_code==202,r.text
    task=c.get('/api/import/%s'%r.json()['id']).json()
    assert task['status']=='completed',task
    assert task['success_count']==1,task
    notes=c.get('/api/notes/').json()
    assert notes['total']==1,notes
    assert c.delete('/api/sources/%s'%source['id'],headers=headers).status_code in (200,204)
    assert c.get('/api/notes/').json()['total']==1
    assert c.post('/api/auth/logout',headers=headers).status_code==200
    assert c.get('/api/notes/').status_code==401
print('PASS: real app authenticated source import and note retention')
'''
        with tempfile.TemporaryDirectory() as cwd:
            env = {k: v for k, v in os.environ.items() if not k.startswith(('PKM_', 'ANTHROPIC_', 'DEEPSEEK_', 'OPENAI_', 'DATABASE_', 'AI_', 'SECRET_', 'ZHIHU_', 'DEBUG'))}
            env['PYTHONPATH'] = backend
            result = subprocess.run([sys.executable, '-B', '-c', code], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

if __name__ == '__main__':
    unittest.main()
