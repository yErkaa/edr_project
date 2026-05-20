from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from db import Base


class AgentModel(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(String(64), unique=True, index=True, nullable=False)
    hostname = Column(String(256), nullable=True)
    ip_address = Column(String(64), nullable=True)
    os_info = Column(String(256), nullable=True)
    last_seen = Column(DateTime, default=datetime.utcnow)
    is_online = Column(Boolean, default=True)
    registered_at = Column(DateTime, default=datetime.utcnow)


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(String(64), ForeignKey("agents.agent_id"), index=True, nullable=True)
    user = Column(String(128), index=True, nullable=True)
    event_type = Column(String(64), index=True, nullable=False)
    severity = Column(String(10), index=True, nullable=True)
    details = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(String(64), index=True, nullable=True)
    alert_type = Column(String(64), index=True, nullable=False)
    severity = Column(String(10), index=True, nullable=False)
    description = Column(Text, nullable=True)
    is_resolved = Column(Boolean, default=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


class PDFile(Base):
    __tablename__ = "pd_files"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(String(64), index=True, nullable=True)
    file_path = Column(String(1024), nullable=False)
    file_name = Column(String(256), nullable=True)
    pii_types = Column(String(256), nullable=True)
    confidence = Column(Float, default=0.0)
    is_blocked = Column(Boolean, default=False)
    pending_quarantine = Column(Boolean, default=False)
    detected_at = Column(DateTime, default=datetime.utcnow)


class QuarantineFile(Base):
    __tablename__ = "quarantine_files"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(String(64), index=True, nullable=True)
    original_path = Column(String(1024), nullable=False)
    quarantine_path = Column(String(1024), nullable=False)
    filename = Column(String(256), nullable=True)
    pii_types = Column(String(256), nullable=True)
    confidence = Column(Float, default=0.0)
    timestamp = Column(DateTime, default=datetime.utcnow)
    is_restored = Column(Boolean, default=False)
    pending_restore = Column(Boolean, default=False)


class Screenshot(Base):
    __tablename__ = "screenshots"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(String(64), index=True, nullable=True)
    event_type = Column(String(64), nullable=True)
    filename = Column(String(256), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(128), unique=True, index=True, nullable=False)
    hashed_password = Column(String(256), nullable=False)
    role = Column(String(20), default="viewer")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    privileges = relationship("Privilege", back_populates="user_obj")


class Privilege(Base):
    __tablename__ = "privileges"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    agent_id = Column(String(64), index=True, nullable=True)
    can_unlock_files = Column(Boolean, default=False)
    can_view_pii = Column(Boolean, default=False)
    granted_by = Column(String(128), nullable=True)
    granted_at = Column(DateTime, default=datetime.utcnow)

    user_obj = relationship("User", back_populates="privileges")
