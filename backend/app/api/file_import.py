"""Inert UTF-8 Markdown/text uploads; never evaluate metadata or fetch resources."""
import hashlib
import re
from dataclasses import dataclass
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.note import Note

router = APIRouter(prefix="/file-import", tags=["file-import"])
MAX_FILE_BYTES = 2 * 1024 * 1024
_READ_CHUNK = 64 * 1024


class PreviewResponse(BaseModel):
    filename: str
    title: str
    chars: int
    format: Literal["markdown", "text"]
    images_remote_warning: bool
    content_hash: str


class CommitResponse(BaseModel):
    status: Literal["imported", "duplicate"]
    note_id: int
    title: str
    characters: int


@dataclass(frozen=True)
class TextUpload:
    filename: str
    title: str
    content: str
    format: str
    content_hash: str
    images_remote_warning: bool


async def _read_text_upload(file: UploadFile) -> TextUpload:
    try:
        # Both Windows and POSIX separators are untrusted metadata, never paths.
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or any(ord(char) < 32 or ord(char) == 127 for char in filename):
            raise HTTPException(422, "A valid Markdown or text filename is required")
        stem, separator, extension = filename.rpartition(".")
        extension = extension.lower()
        if not separator or extension not in {"md", "markdown", "txt"}:
            raise HTTPException(415, "Only .md, .markdown and .txt files are supported")
        data = bytearray()
        while True:
            # Read at most one excess byte, regardless of Content-Length or size hints.
            chunk = await file.read(min(_READ_CHUNK, MAX_FILE_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_FILE_BYTES:
                raise HTTPException(413, "File exceeds the 2 MiB limit")
        try:
            content = data.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            raise HTTPException(422, "File must contain valid UTF-8 text") from None
        if "\x00" in content:
            raise HTTPException(422, "NUL bytes are not allowed in text files")
        if not content.strip():
            raise HTTPException(422, "File text must not be empty")
        # Only BOM and line endings are normalized for identity. Stored text is not
        # stripped, rendered, parsed as YAML, or otherwise rewritten.
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        heading = re.search(r"^ {0,3}#[ \t]+([^\r\n]+)", normalized, re.MULTILINE)
        title = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(1)).strip() if heading else (stem or filename)
        title = (title or stem or filename)[:500]
        # Deliberately conservative warning, not a Markdown/HTML interpreter.
        # Includes inline/reference Markdown and HTML image syntax. No URL opens.
        has_image = bool(re.search(r"!\[|<img\b", content, re.IGNORECASE))
        has_remote = bool(re.search(r"(?:https?://|(?<!:)//)[^\s<>]+", content, re.IGNORECASE))
        return TextUpload(filename, title, content, "text" if extension == "txt" else "markdown", digest, has_image and has_remote)
    finally:
        await file.close()


@router.post("/preview", response_model=PreviewResponse)
async def preview_file(file: UploadFile = File(...)):
    uploaded = await _read_text_upload(file)
    return PreviewResponse(filename=uploaded.filename, title=uploaded.title,
                           chars=len(uploaded.content), format=uploaded.format,
                           images_remote_warning=uploaded.images_remote_warning,
                           content_hash=uploaded.content_hash)


@router.post("/commit", response_model=CommitResponse)
async def commit_file(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    uploaded = await _read_text_upload(file)
    chosen_title = title.strip() if title is not None else uploaded.title
    if not chosen_title:
        chosen_title = uploaded.title
    if len(chosen_title) > 500 or any(ord(char) < 32 or ord(char) == 127 for char in chosen_title):
        raise HTTPException(422, "Title must be plain text of at most 500 characters")
    try:
        existing = db.query(Note).filter(Note.source == "file", Note.source_id == uploaded.content_hash).order_by(Note.id).first()
        if existing is not None:
            return CommitResponse(status="duplicate", note_id=existing.id,
                                  title=existing.title, characters=len(existing.content))
        note = Note(title=chosen_title, content=uploaded.content, source="file",
                    source_id=uploaded.content_hash, content_status="full",
                    ai_processed=0, images=[], image_total=0, image_failed=0)
        db.add(note)
        db.commit()
        db.refresh(note)
        return CommitResponse(status="imported", note_id=note.id,
                              title=note.title, characters=len(note.content))
    except SQLAlchemyError:
        db.rollback()
        # Do not echo database parameters (which include the uploaded text).
        raise HTTPException(500, "Unable to import file") from None
