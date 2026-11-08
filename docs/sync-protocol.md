# PKM read-only synchronization protocol 1

This API lets a local client retain an offline copy. It is **not** a device-management service, upload API, cursor log, change journal, or remote wipe facility. The index is fetched in full on every sync. Only changed note bodies and absent/changed images need incremental transfer.

## Authentication and deployment

Mount `app.api.sync.router` at `/api` in `main.create_app`. Existing global authentication protects **all** sync and image routes, with no separate token or anonymous exception. Clients log in through `/api/auth/login`, retain the session cookie, and send `Origin: <configured PKM_PUBLIC_ORIGIN>` for login and `POST /api/sync/batch`, exactly as required by existing auth. Use the deployed HTTPS origin; do not bypass certificate verification. No response exports Cookie files, environment variables, settings, task configurations, or server paths. Note text and note image metadata are user content and are returned unchanged, not semantically secret-redacted.

## GET /api/sync/manifest

Response (200):

```json
{
  "protocol": 1,
  "snapshot": "<64 lowercase hex>",
  "scope": {"mode": "all", "source_ids": []},
  "notes": [{"id": 1, "revision": "<64 lowercase hex>"}],
  "attachments": [{
    "filename": "<64 lowercase hex>.png",
    "sha256": "<64 lowercase hex>",
    "size": 123,
    "available": true,
    "url": "/api/attachments/<64 lowercase hex>.png"
  }]
}
```

- Complete note index **within the selected scope**, sorted by ascending integer `id`; attachment index sorted by `filename`, deduplicated across included notes.
- Default (no scope/filter parameters) is `scope={"mode":"all","source_ids":[]}` and includes legacy notes without source membership. `?scope=sources&source_ids=1&source_ids=2` selects the union of notes explicitly associated with those saved source IDs. Supplying repeated `source_ids` without `scope` also selects sources mode. `?scope=sources` explicitly selects **zero** sources, returning empty notes/attachments; it is never treated as all. An explicit `scope=all` must not include `source_ids`.
- Query `source_ids` must be ASCII positive integer strings, unique after integer parsing, at most 1000 entries. No empty, signed, whitespace-padded, boolean or floating values; invalid filters return 422. IDs in the response scope are sorted ascending. Unknown/deleted sources yield no membership matches, not a guessed match.
- Membership is a unique `(source_id,note_id)` association populated from the trusted saved-source ID during new **and existing** Zhihu imports. Overlapping selected sources return a note only once. A title, source platform/category, collection label or original platform item ID never establishes membership. Legacy notes remain in all mode, but are absent from filtered manifests until a source import explicitly associates them.
- Hard limit: 100000 notes and 100000 unique referenced attachments. Exceeding either returns 413; it never returns a partial manifest. An empty repository returns empty arrays and a valid snapshot.
- Only valid local `images[].path` references enter the attachment index: lowercase SHA-256 plus `jpg`, `jpeg`, `png`, `gif`, or `webp`. Remote-only image metadata stays in the note, not the local attachment index. Unreferenced files are not enumerated. No directory or filesystem path is accepted.
- `sha256` is the expected content hash from the immutable filename. Every entry always includes `available`, `size`, and `url`. Missing, unreadable, symlinked, non-regular, or hash-invalid files remain indexed with **`available:false,size:null`**. Do not request those entries; keep an existing valid local copy and retry on a later sync. They are not silently treated as deleted notes or deleted local images. Available files are hashed and sized from their bytes.
- URLs intentionally stay `/api/attachments/<filename>` (existing authenticated route). `/api/sync/attachments/<filename>` is an equivalent compatibility alias.
- Successful manifest and batch responses use `Cache-Control: private, no-store`.

## GET /api/sync/sources

Returns only sorted safe source labels: `[{"id":1,"name":"Reading list","platform":"zhihu"}]`. The read-only device allowlist permits this GET alongside manifest/batch. No cookie, config, collection URL, task, scheduler or other source properties are included. The route performs SELECTs only and uses `Cache-Control: private, no-store`. This is discovery for selecting scope, not permission to modify sources.

## Canonical revisions and snapshot

Each note revision hashes its complete public `NoteResponse` JSON **without** the `revision` field. This includes title, full body, source metadata, tags, AI fields, content status/warnings, images and their metadata, counters, creation and update timestamps. Null and default fields are retained. Datetimes use Pydantic JSON serialization. Object keys are recursively lexicographically sorted; arrays retain meaningful order (including images/tags), Unicode is not ASCII-escaped, there is no whitespace, NaN/Infinity are disallowed. The exact implementation is Python `json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')`, then SHA-256 lowercase hex. No Unicode normalization is performed. Revision is not based solely on second-resolution timestamps.

For **all** mode, `snapshot` retains the legacy canonical hash input `{"protocol":1,"notes":[...],"attachments":[...]}`; the extra response `scope` field is deliberately excluded so old all-mode snapshots/clients remain compatible. For **sources** mode the canonical hash input is `{"protocol":1,"scope":{"mode":"sources","source_ids":[...]},"notes":[...],"attachments":[...]}`. Scope IDs are normalized/sorted before hashing. Thus different scopes cannot share a batch snapshot even if their note sets are identical; all and sources modes are also isolated. Note additions/deletions within the scope, body/AI/image changes, and attachment availability/size changes invalidate a prior snapshot. A source membership change that alters the included note set also invalidates the filtered snapshot. Protocol is included for domain/version separation. Neither a revision table nor a stored revision column is needed.

## POST /api/sync/batch

Request:

```json
{"snapshot":"<manifest snapshot>","ids":[1,2]}
```

`ids` must contain 1–50 **unique positive strict JSON integers** (no booleans, floats or numeric strings). Snapshot must be exactly 64 lowercase hexadecimal characters. Unknown request properties are rejected. Invalid inputs return 422.

For a filtered manifest, send the identical scope, e.g. `{"snapshot":"<filtered snapshot>","ids":[1,2],"scope":"sources","source_ids":[1]}`. Body `source_ids` must be a list of at most 1000 unique positive **strict JSON integers** (no strings, booleans or floats); empty `[]` is valid and explicitly selects zero sources. Missing scope defaults to sources when `source_ids` is supplied, otherwise all. `scope:"sources"` without source IDs is also an empty selection. Explicit nulls and `scope:"all"` with any source_ids (even empty) are rejected. Scope ID ordering is immaterial. A batch using another scope returns 409, even when it would include the same note IDs. All-mode legacy request bodies remain valid.

Success (200):

```json
{"snapshot":"<same snapshot>","notes":[{"id":1,"title":"...","content":"...","revision":"..."}]}
```

Each note is the entire existing `NoteResponse` plus `revision`; response order matches the requested IDs. The abbreviated example above omits the other NoteResponse fields only for readability.

Before responding, the server recalculates the **entire** current manifest. If it differs, it returns **409** with:

```json
{"detail":{"code":"snapshot_changed","restart":true,"message":"Fetch a new complete manifest and restart sync"}}
```

No partial note batch is returned. A requested ID absent from an otherwise matching snapshot returns 404 `{"detail":{"code":"note_not_in_snapshot"}}`. A deleted ID requested against the old snapshot returns 409 first, not a misleading empty success.

## Local-client algorithm and deletions

1. Fetch the complete manifest; compare revisions to local remote-revision records.
2. Request new/changed notes in batches of at most 50 with exactly that snapshot. Validate note revisions if desired. Download available attachments absent locally or failing their expected hash; verify SHA-256 and size before publishing local files.
3. On 409 discard/restart the current sync plan with a new complete manifest. Stage results and only promote a completed plan atomically where possible. Existing local files need not be deleted during restart.
4. For **all mode only**, once all planned batches succeed, compare the complete manifest ID set to the previous remote-ID set. IDs now absent are **retained locally and marked `remote_deleted`**; do not erase local text, annotations, or attachments. IDs that reappear clear that marker after successfully downloading the current note.
5. **Only an all-mode manifest may drive `remote_deleted` marking.** A sources-mode manifest is complete only for its selection; missing IDs may belong to unselected sources or legacy notes. Filtered sync (including an empty selection or switching scope) must never mark absent local notes `remote_deleted` or erase them. Store/validate scope with the sync plan and send it with every batch; restart instead of mixing staged results from different scopes.
6. A failed/incomplete manifest or interrupted sync must never be used to mark deletions. All-mode manifest completeness is the deletion signal; there are no tombstones or cursor-watermark guarantees.

Each request uses a fresh read-only SQLAlchemy Session bound to the existing injected DB engine. SQLite explicitly executes `BEGIN` before reads (sqlite3 legacy SELECT alone does not begin a read transaction). All note data and the returned batch are materialized from that single read snapshot, with autoflush disabled and rollback on close; no commits, schema changes, or query-induced writes occur. A concurrent commit after a batch's snapshot read can legitimately yield the old consistent batch; the next request detects the new snapshot and returns 409. This does **not** lock the repository across HTTP requests or promise freshness at the instant the response arrives.

Attachment bytes live outside SQLite. Their immutable content-addressed storage and hash verification provide consistency, not an impossible cross-filesystem transaction. Deletion after manifest issuance may cause a later image download to return 404; refresh the manifest and restart/retry. The compatibility download alias reuses the existing image handler and its security/cache behavior. Clients must verify downloaded bytes because the existing handler does not itself hash every download.
