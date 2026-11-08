"""Read-only live acceptance for explicitly enabled anonymous sync testing."""
import httpx

ORIGIN = 'http://8.133.220.164:8091'
with httpx.Client(base_url=ORIGIN, headers={'Origin': ORIGIN}, timeout=30, follow_redirects=False) as client:
    r = client.get('/api/sync/manifest')
    r.raise_for_status()
    assert r.headers.get('x-pkm-test-public-sync') == 'true'
    manifest = r.json()
    print('Anonymous manifest: OK')
    client.get('/api/sync/sources').raise_for_status()
    print('Anonymous source list: OK')
    for path in ['/api/notes/', '/api/sources/', '/api/auth/me', '/docs']:
        assert client.get(path).status_code == 401, path
    # Never send a potentially mutating request to live write routes.
    print('Admin and general notes endpoints: still require login')
    assert client.post('/api/sync/batch', json={}).status_code == 422
    ids = [note['id'] for note in manifest['notes'][:50]]
    if ids:
        batch = client.post('/api/sync/batch', json={'snapshot': manifest['snapshot'], 'ids': ids})
        batch.raise_for_status()
        assert len(batch.json()['notes']) == len(ids)
    for attachment in manifest['attachments']:
        if attachment['available']:
            import hashlib
            image = client.get(attachment['url'])
            image.raise_for_status()
            assert hashlib.sha256(image.content).hexdigest() == attachment['sha256']
    print('Anonymous batch and available image hashes: OK')
    print('Live read-only acceptance passed; no notes changed')
