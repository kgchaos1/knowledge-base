"""Explicit saved-source membership; never infer it from note metadata."""
from sqlalchemy import Column, ForeignKey, Integer

from app.core.database import Base


class NoteSource(Base):
    __tablename__ = "note_sources"

    # A composite primary key makes repeat imports idempotent at the DB boundary.
    # Deleting a membership/source must never cascade into the note itself.
    source_id = Column(Integer, ForeignKey("data_sources.id", ondelete="CASCADE"), primary_key=True)
    note_id = Column(Integer, ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True, index=True)
