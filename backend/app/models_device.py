"""Additive device tables; importing registers them with the existing Base.

No relationships to notes or backups. Secrets are never persisted in plaintext.
"""
from sqlalchemy import Boolean, Column, Float, Integer, String

from app.core.database import Base


class Device(Base):
    __tablename__ = "devices"

    id = Column(String(32), primary_key=True)
    name = Column(String(120), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    created_at = Column(Float, nullable=False)
    last_seen = Column(Float, nullable=True)
    revoked = Column(Boolean, nullable=False, default=False)


class Pairing(Base):
    __tablename__ = "device_pairings"

    id = Column(Integer, primary_key=True)
    code_hash = Column(String(64), nullable=False, unique=True)
    name = Column(String(120), nullable=False)
    created_at = Column(Float, nullable=False)
    expires_at = Column(Float, nullable=False, index=True)
