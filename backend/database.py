# database.py — SQLAlchemy setup and ORM models
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Boolean,
    Enum, TIMESTAMP, ForeignKey, SmallInteger
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from config import settings

# Build MySQL connection URL
DATABASE_URL = (
    f"mysql+pymysql://{settings.DB_USER}:{settings.DB_PASSWORD}"
    f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ---- ORM Models ----

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    gmail_token = Column(Text, nullable=True)          # JSON OAuth2 token
    automation_enabled = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    leads = relationship("Lead", back_populates="user", cascade="all, delete")


class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    from_email = Column(String(255), nullable=False)
    from_name = Column(String(255), nullable=True)
    subject = Column(String(500), nullable=True)
    message = Column(Text, nullable=True)
    thread_id = Column(String(255), nullable=True)     # Gmail thread ID
    message_id = Column(String(255), nullable=True)    # Gmail message ID
    status = Column(Enum("new", "replied", "converted", "unsubscribed"), default="new")
    date_received = Column(TIMESTAMP, default=datetime.utcnow)
    has_replied = Column(Boolean, default=False)

    user = relationship("User", back_populates="leads")
    followups = relationship("FollowUp", back_populates="lead", cascade="all, delete")


class FollowUp(Base):
    __tablename__ = "followups"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    followup_number = Column(SmallInteger, nullable=False)  # 1, 2, or 3
    scheduled_date = Column(TIMESTAMP, nullable=False)
    sent_date = Column(TIMESTAMP, nullable=True)
    status = Column(Enum("pending", "sent", "skipped", "failed"), default="pending")
    generated_body = Column(Text, nullable=True)        # AI-generated email content
    error_message = Column(Text, nullable=True)

    lead = relationship("Lead", back_populates="followups")


# Dependency for FastAPI routes
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
