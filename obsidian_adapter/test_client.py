import hashlib
import unittest
from .client import Client,SnapshotError

class ClientTests(unittest.TestCase):
    def test_origin_and_http_opt_in(self):
        for server in ['http://example.org','https://user:pass@example.org','https://example.org/path']:
            with self.assertRaises(ValueError):Client(server)
        self.assertEqual(Client('https://example.org/').server,'https://example.org')

    def test_changed_snapshot_and_attachment_integrity(self):
        import json
        body=b'fake-image';name=hashlib.sha256(body).hexdigest()+'.png'
        manifest={'protocol':1,'snapshot':'a'*64,'notes':[{'id':1,'revision':'rev'}],
            'attachments':[{'filename':name,'sha256':name.split('.')[0],'size':len(body),'available':True}]}
        client=Client('https://example.org')
        def fake(path,payload=None,limit=None):
            if path=='/api/sync/manifest':return json.dumps(manifest).encode()
            if path=='/api/sync/batch':return json.dumps({'snapshot':'a'*64,'notes':[{'id':1,'revision':'rev','title':'test'}]}).encode()
            return body
        client._request=fake
        notes,attachments,_=client.fetch()
        self.assertEqual(len(notes),1);self.assertEqual(attachments[name],body)
        manifest['attachments'][0]['sha256']='b'*64
        with self.assertRaises(SnapshotError):client.fetch()
