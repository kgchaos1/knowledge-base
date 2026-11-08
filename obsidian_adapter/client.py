"""Read-only PKM snapshot fetch, no dependency on Obsidian or HTTP plugins."""
import hashlib
import http.cookiejar
import json
import re
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

class SnapshotError(RuntimeError):
    pass

class Client:
    def __init__(self, server, *, allow_http=False):
        parsed=urlsplit(server)
        if (parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('','/') or parsed.query or parsed.fragment or any(c.isspace() for c in server)):
            raise ValueError('Use a server origin without paths or credentials')
        if parsed.scheme=='http' and not allow_http:
            raise ValueError('HTTP requires explicit risk acceptance')
        self.server=server.rstrip('/')
        self.opener=build_opener(NoRedirect,HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _request(self,path,payload=None,limit=32*1024*1024):
        headers={'Origin':self.server,'Accept':'application/json'}
        data=None
        if payload is not None:
            headers['Content-Type']='application/json';data=json.dumps(payload).encode()
        try:
            with self.opener.open(Request(self.server+path,data=data,headers=headers),timeout=30) as response:
                body=response.read(limit+1)
                if len(body)>limit:raise SnapshotError('Response exceeds safety limit')
                return body
        except Exception:
            raise SnapshotError('Server request failed; check connection and authentication') from None

    def login(self,password):
        self._request('/api/auth/login',{'password':password},limit=4096)

    def manifest(self):
        data=json.loads(self._request('/api/sync/manifest'))
        if (data.get('protocol')!=1 or not isinstance(data.get('notes'),list) or not isinstance(data.get('attachments'),list)
                or not re.fullmatch('[0-9a-f]{64}',str(data.get('snapshot','')))):
            raise SnapshotError('Invalid complete snapshot index')
        return data

    def fetch(self):
        manifest=self.manifest();notes=[];seen=set()
        entries=manifest['notes']
        for offset in range(0,len(entries),50):
            chunk=entries[offset:offset+50];ids=[e['id'] for e in chunk]
            expected={e['id']:e['revision'] for e in chunk}
            data=json.loads(self._request('/api/sync/batch',{'snapshot':manifest['snapshot'],'ids':ids}))
            batch=data.get('notes')
            if data.get('snapshot')!=manifest['snapshot'] or not isinstance(batch,list) or len(batch)!=len(ids):
                raise SnapshotError('Incomplete or changed snapshot; retry export')
            for note in batch:
                if note.get('id') not in expected or note['id'] in seen or note.get('revision')!=expected[note['id']]:
                    raise SnapshotError('Invalid note revision')
                seen.add(note['id']);notes.append(note)
        attachments={}
        for item in manifest['attachments']:
            name=item.get('filename','')
            if not re.fullmatch(r'[0-9a-f]{64}\.(?:jpg|jpeg|png|gif|webp)',name) or item.get('available') is not True:
                raise SnapshotError('Unavailable or invalid attachment')
            size=item.get('size')
            if type(size) is not int or not 0<=size<=32*1024*1024:
                raise SnapshotError('Invalid attachment size')
            body=self._request('/api/attachments/'+name,limit=32*1024*1024)
            if len(body)!=size or hashlib.sha256(body).hexdigest()!=item.get('sha256'):
                raise SnapshotError('Attachment integrity check failed')
            attachments[name]=body
        if self.manifest()['snapshot']!=manifest['snapshot']:
            raise SnapshotError('Snapshot changed during export; retry')
        return notes,attachments,manifest['snapshot']
