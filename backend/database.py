# database.py — SQLAlchemy with SQLite (no external DB required).
# Switch to Postgres/MySQL later by changing DATABASE_URL in .env.

from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Text,
    Boolean, Enum, DateTime, ForeignKey, SmallInteger,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from config import settings

# ── Engine ────────────────────────────────────────────────────
# check_same_thread=False is required for SQLite + FastAPI
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ── Models ────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id                 = Column(Integer, primary_key=True, index=True)
    email              = Column(String(255), unique=True, nullable=False, index=True)
    password_hash      = Column(String(255), nullable=False)
    gmail_token        = Column(Text, nullable=True)        # JSON OAuth2 token
    automation_enabled = Column(Boolean, default=False)
    # ── Subscription / billing ────────────────────────────────
    plan               = Column(String(50), default="free") # free | basic | pro | business
    paypal_sub_id      = Column(String(255), nullable=True) # PayPal subscription ID
    sub_status         = Column(String(50), default="inactive")  # active | inactive | cancelled
    sub_expires_at     = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    leads    = relationship("Lead",    back_populates="user", cascade="all, delete")
    payments = relationship("Payment", back_populates="user", cascade="all, delete")


class Lead(Base):
    __tablename__ = "leads"

    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False)
    from_email    = Column(String(255), nullable=False)
    from_name     = Column(String(255), nullable=True)
    subject       = Column(String(500), nullable=True)
    message       = Column(Text, nullable=True)
    thread_id     = Column(String(255), nullable=True)
    message_id    = Column(String(255), nullable=True)
    status        = Column(
        Enum("new", "replied", "converted", "unsubscribed"),
        default="new",
    )
    date_received = Column(DateTime, default=datetime.utcnow)
    has_replied   = Column(Boolean, default=False)

    user     = relationship("User",     back_populates="leads")
    followups = relationship("FollowUp", back_populates="lead", cascade="all, delete")


class FollowUp(Base):
    __tablename__ = "followups"

    id              = Column(Integer, primary_key=True, index=True)
    lead_id         = Column(Integer, ForeignKey("leads.id"), nullable=False)
    followup_number = Column(SmallInteger, nullable=False)   # 1 = Day3, 2 = Day5, 3 = Day7
    scheduled_date  = Column(DateTime, nullable=False)
    sent_date       = Column(DateTime, nullable=True)
    status          = Column(
        Enum("pending", "sent", "skipped", "failed"),
        default="pending",
    )
    generated_body  = Column(Text, nullable=True)
    error_message   = Column(Text, nullable=True)

    lead = relationship("Lead", back_populates="followups")


class Payment(Base):
    """Records every PayPal transaction for auditing and plan management."""
    __tablename__ = "payments"

    id              = Column(Integer, primary_key=True, index=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=False)
    # PayPal order/subscription/capture IDs
    paypal_order_id = Column(String(255), nullable=True, index=True)
    paypal_sub_id   = Column(String(255), nullable=True, index=True)
    paypal_capture_id = Column(String(255), nullable=True)
    # Plan purchased
    plan            = Column(String(50), nullable=False)
    amount          = Column(String(20), nullable=True)     # e.g. "29.00"
    currency        = Column(String(10), default="USD")
    # pending | completed | failed | refunded
    status          = Column(String(50), default="pending")
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="payments")


# ── DB dependency for FastAPI routes ──────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Safe to call multiple times."""
    Base.metadata.create_all(bind=engine)
