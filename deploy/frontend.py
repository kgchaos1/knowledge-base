"""Deploy only HTML to the verified current release, preserve the previous UI."""
import json,hashlib
from pathlib import Path
import os
from remote import connect
os.chdir(Path(__file__).resolve().parents[1])
c=connect()
try:
 release=json.loads(Path('ip-release-state.json').read_text())['release']
 _,out,err=c.exec_command("docker inspect pkm-web --format '{{json .Mounts}}'",timeout=15)
 mounts=json.loads(out.read());assert out.channel.recv_exit_status()==0
 assert any(m['Source']==release+'/frontend' and m['Destination']=='/usr/share/nginx/html' for m in mounts)
 s=c.open_sftp();target=release+'/frontend/index.html'
 with s.open(target) as f:previous=f.read()
 with s.open(target+'.before-card-actions','wb') as f:f.write(previous)
 html=Path('frontend/index.html').read_bytes()
 with s.open(target,'wb') as f:f.write(html)
 with s.open(target) as f:assert hashlib.sha256(f.read()).digest()==hashlib.sha256(html).digest()
 s.close();print('Current frontend updated and checksum verified; backend unchanged')
finally:c.close()
