"""Versioned upgrade, preserve login/env, backup SQLite and attachments."""
import argparse,json,shlex,time
from pathlib import Path
import os
from remote import connect
os.chdir(Path(__file__).resolve().parents[1])
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--test-public-sync', choices=['on','off'], help='TEST ONLY: expose read-only sync without pairing; admin stays protected')
args=parser.parse_args()
c=connect()
def run(cmd,timeout=90):
    _,o,e=c.exec_command(cmd,timeout=timeout);out=o.read().decode();err=e.read().decode();status=o.channel.recv_exit_status()
    if status:raise RuntimeError('Remote failed '+str(status)+' '+err[-500:])
    return out
try:
    state=json.loads(Path('ip-release-state.json').read_text());old=state['release']
    stamp=time.strftime('%Y%m%d-%H%M%S');root='/root/pkm-system';new=root+'/releases/'+stamp
    s=c.open_sftp()
    with s.open(old+'/compose.json') as f:compose=json.loads(f.read())
    with s.open(old+'/nginx.conf') as f:nginx=f.read()
    def mkdir(p):
        try:s.stat(p)
        except FileNotFoundError:s.mkdir(p)
    mkdir(new);mkdir(new+'/app');mkdir(new+'/frontend')
    for p in Path('backend/app').rglob('*.py'):
        rel=p.relative_to('backend/app').as_posix();parent=new+'/app'
        for part in rel.split('/')[:-1]:parent+='/'+part;mkdir(parent)
        s.put(str(p),new+'/app/'+rel)
    s.put('frontend/index.html',new+'/frontend/index.html')
    for service in compose['services'].values():
        service['volumes']=[mount.replace(old+'/',new+'/') for mount in service['volumes']]
    if args.test_public_sync is not None:
        environment=compose['services']['pkm-api'].setdefault('environment', {})
        value='true' if args.test_public_sync == 'on' else 'false'
        if isinstance(environment, dict):
            environment['PKM_TEST_PUBLIC_SYNC']=value
        else:
            environment[:]=[item for item in environment if item.split('=',1)[0] != 'PKM_TEST_PUBLIC_SYNC']
            environment.append('PKM_TEST_PUBLIC_SYNC='+value)
    for name,data in [('compose.json',json.dumps(compose).encode()),('nginx.conf',nginx)]:
        with s.open(new+'/'+name,'wb') as f:f.write(data)
        s.chmod(new+'/'+name,0o600 if name=='compose.json' else 0o644)
    run('docker-compose -p pkm-system -f '+new+'/compose.json config --quiet')
    # Quiesce all PKM writes, then use SQLite backup API and capture attachment tree.
    run('docker stop pkm-api')
    backup_code="import sqlite3,tarfile,pathlib; a=sqlite3.connect('/data/pkm.db'); b=sqlite3.connect('/backups/pre-content-"+stamp+".db'); a.backup(b);b.close(); p=pathlib.Path('/data/attachments'); t=tarfile.open('/backups/attachments-"+stamp+".tar.gz','w:gz'); t.add(str(p),arcname='attachments') if p.exists() else None;t.close(); print('Database and attachment backup saved')"
    image=compose['services']['pkm-api']['image']
    print(run('docker run --rm -v '+root+'/data:/data -v '+root+'/backups:/backups '+image+' python -c '+shlex.quote(backup_code)),flush=True)
    try:
        run('docker-compose -p pkm-system -f '+new+'/compose.json up -d --no-build')
        for attempt in range(15):
            try: print(run('curl -fsS --max-time 3 http://127.0.0.1:8091/api/health'));break
            except RuntimeError:
                if attempt==14:raise
                time.sleep(1)
    except Exception:
        run('docker-compose -p pkm-system -f '+old+'/compose.json up -d --no-build')
        raise
    state.update(release=new,previous_release=old,content_backup='pre-content-'+stamp+'.db')
    Path('ip-release-state.json').write_text(json.dumps(state))
    s.close();print('Release:',new)
finally:c.close()
