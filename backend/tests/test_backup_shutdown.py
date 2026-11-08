import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class BackupShutdownTests(unittest.TestCase):
    def test_cancel_drains_archive_registration(self):
        code=r'''
import asyncio,threading
from pathlib import Path
from unittest.mock import patch
from app.core import backup_service as s
s.ROOT=Path('archives');s.ROOT.mkdir();s.CONFIG=s.ROOT/'schedule.json'
entered=threading.Event();release=threading.Event()
def create(*args):
 entered.set();release.wait(10)
 p=s.ROOT/'test.zip';p.write_bytes(b'archive')
 return {'path':str(p),'sha256':'a'*64,'notes_count':3}
async def run():
 with patch.object(s,'create_archive',create),patch.object(s,'verify_archive',return_value={'verified':True}),patch.object(s,'db_path',return_value=Path('unused.db')):
  task=asyncio.create_task(s.make_backup())
  await asyncio.to_thread(entered.wait,5)
  task.cancel();release.set()
  try:await task
  except asyncio.CancelledError:pass
  assert len(s.list_backups())==1
  assert s.schedule_settings()['last_success']
  assert not s.STATE['running']
asyncio.run(run())
'''
        with tempfile.TemporaryDirectory() as cwd:
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            result=subprocess.run([sys.executable,'-B','-c',code],cwd=cwd,env=env,capture_output=True,text=True,timeout=25)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
