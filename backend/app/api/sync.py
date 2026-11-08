"""Authenticated by main's global middleware; parent mounts this router at /api."""
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import attachments
from app.core.database import get_db
from app.core.sync_snapshot import SyncLimitError, load_snapshot, normalize_scope, read_session
from app.models.note import DataSource
from app.models.schemas import NoteResponse

router = APIRouter(prefix="/sync", tags=["sync"])


class NoteRevision(BaseModel):
    id: int
    revision: str


class AttachmentIndex(BaseModel):
    filename: str
    sha256: str
    size: int | None
    available: bool
    url: str


class SyncScope(BaseModel):
    mode: Literal["all", "sources"]
    source_ids: list[int]


class ManifestResponse(BaseModel):
    protocol: int
    snapshot: str
    notes: list[NoteRevision]
    attachments: list[AttachmentIndex]
    scope: SyncScope


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    ids: list[Annotated[StrictInt, Field(gt=0)]] = Field(min_length=1, max_length=50)
    scope: Literal["all", "sources"] | None = None
    source_ids: list[Annotated[StrictInt, Field(gt=0)]] | None = Field(default=None, max_length=1000)

    @field_validator("ids")
    @classmethod
    def unique_ids(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("ids must be unique")
        return value

    @model_validator(mode="after")
    def valid_scope(self):
        if "source_ids" in self.model_fields_set and self.source_ids is None:
            raise ValueError("source_ids must be a list")
        if "scope" in self.model_fields_set and self.scope is None:
            raise ValueError("scope must be all or sources")
        normalize_scope(self.scope, self.source_ids)
        return self


class SyncNoteResponse(NoteResponse):
    revision: str


class BatchResponse(BaseModel):
    snapshot: str
    notes: list[SyncNoteResponse]


class SourceResponse(BaseModel):
    id: int
    name: str
    platform: str


def _snapshot(db, scope=None, source_ids=None):
    try:
        with read_session(db) as reader:
            return load_snapshot(reader, attachments.ROOT, scope=scope, source_ids=source_ids)
    except SyncLimitError as exc:
        raise HTTPException(413, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/sources", response_model=list[SourceResponse])
def sources(response: Response, db: Session = Depends(get_db)):
    """Device-safe public source labels only; no config, task, cookie or URL."""
    response.headers["Cache-Control"] = "private, no-store"
    with read_session(db) as reader:
        return [dict(row) for row in reader.execute(select(
            DataSource.id, DataSource.name, DataSource.platform,
        ).order_by(DataSource.id)).mappings()]


@router.get("/manifest", response_model=ManifestResponse)
def manifest(response: Response, db: Session = Depends(get_db),
             scope: Literal["all", "sources"] | None = Query(default=None),
             source_ids: list[str] | None = Query(default=None)):
    response.headers["Cache-Control"] = "private, no-store"
    if source_ids is not None:
        # HTTP query parameters are text, but reject signs/whitespace/float/bool
        # spellings rather than letting Pydantic permissively coerce them.
        if len(source_ids) > 1000 or any(not re.fullmatch(r"[0-9]+", item) for item in source_ids):
            raise HTTPException(422, "source_ids must be at most 1000 positive integer IDs")
        try:
            source_ids = [int(item) for item in source_ids]
        except ValueError:
            raise HTTPException(422, "source_ids contain an invalid integer") from None
    return _snapshot(db, scope, source_ids)[0]


@router.post("/batch", response_model=BatchResponse)
def batch(body: BatchRequest, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    current, notes = _snapshot(db, body.scope, body.source_ids)
    if current["snapshot"] != body.snapshot:
        raise HTTPException(409, {"code": "snapshot_changed", "restart": True,
                                  "message": "Fetch a new complete manifest and restart sync"})
    if any(note_id not in notes for note_id in body.ids):
        raise HTTPException(404, {"code": "note_not_in_snapshot"})
    return {"snapshot": current["snapshot"], "notes": [notes[note_id] for note_id in body.ids]}


@router.get("/attachments/{filename}")
async def attachment(filename: str):
    return await attachments.read_attachment(filename)
