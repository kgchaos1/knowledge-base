"""Real pairing/revoke test; never outputs code/token or touches notes."""
import shlex
from pathlib import Path
from remote import connect
password=(Path(__file__).resolve().parents[1]/'PKM-login-private.txt').read_text(encoding='utf-8').split('登录密码：',1)[1].splitlines()[0]
code=r'''
import sys,httpx
with httpx.Client(base_url='http://pkm-web',headers={'Origin':'http://8.133.220.164:8091'},timeout=30) as admin:
 admin.post('/api/auth/login',json={'password':sys.stdin.read()}).raise_for_status()
 r=admin.post('/api/devices/pairing',json={'name':'Obsidian配对修复验收'});r.raise_for_status();pairing=r.json()['code']
 with httpx.Client(base_url='http://pkm-web',headers={'Origin':'http://8.133.220.164:8091'},timeout=30) as device:
  r=device.post('/api/devices/claim',json={'code':pairing,'name':'Obsidian配对修复验收'});r.raise_for_status();data=r.json()
  try:
   device.headers['Authorization']='Bearer '+data['token']
   device.get('/api/sync/manifest').raise_for_status()
   r=admin.post('/api/devices/claim',json={'code':pairing,'name':'duplicate'});assert r.status_code==401
   print('Real pairing succeeds; reuse returns401 as expected; read-only manifest succeeds')
  finally:admin.post('/api/devices/'+data['device_id']+'/revoke').raise_for_status()
'''
c=connect()
try:
 i,o,e=c.exec_command('docker exec -i pkm-api python -c '+shlex.quote(code),timeout=60)
 i.write(password);i.flush();i.channel.shutdown_write();out=o.read().decode();e.read();status=o.channel.recv_exit_status()
 if status:raise RuntimeError('Pairing verification failed; inspect privately')
 print(out)
finally:c.close()
