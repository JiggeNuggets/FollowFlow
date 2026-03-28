# main.py — FastAPI application entry point
import json
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from config import settings
from database import Base, engine, get_db, User, Lead, FollowUp
from auth import hash_password, verify_password, create_access_token, get_current_user
from gmail_service import get_auth_url, exchange_code_for_token, build_gmail_service, fetch_recent_emails
from ai_service import generate_followup_email
from scheduler import start_scheduler, stop_scheduler, run_followup_check, schedule_followups_for_lead

# Create all tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Gmail Follow-Up Automation API",
    description="AI-powered email follow-up system",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Pydantic Schemas
# ============================================================

class UserRegister(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class LeadResponse(BaseModel):
    id: int
    from_email: str
    from_name: Optional[str]
    subject: Optional[str]
    status: str
    date_received: datetime
    has_replied: bool
    followup_count: int = 0

    class Config:
        from_attributes = True

class FollowUpResponse(BaseModel):
    id: int
    lead_id: int
    followup_number: int
    scheduled_date: datetime
    sent_date: Optional[datetime]
    status: str
    generated_body: Optional[str]

    class Config:
        from_attributes = True

class DashboardStats(BaseModel):
    total_leads: int
    new_leads: int
    replied_leads: int
    followups_sent: int
    followups_pending: int
    automation_enabled: bool
    gmail_connected: bool


# ============================================================
# Lifecycle Events
# ============================================================

@app.on_event("startup")
async def startup_event():
    start_scheduler()

@app.on_event("shutdown")
async def shutdown_event():
    stop_scheduler()


# ============================================================
# Auth Routes
# ============================================================

@app.post("/api/auth/register", response_model=TokenResponse)
def register(data: UserRegister, db: Session = Depends(get_db)):
    """Register a new user account."""
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token({"sub": str(user.id)})
    return {"access_token": token}


@app.post("/api/auth/login", response_model=TokenResponse)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Login with email and password."""
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": str(user.id)})
    return {"access_token": token}


# ============================================================
# Gmail OAuth Routes
# ============================================================

@app.get("/api/gmail/auth-url")
def get_gmail_auth_url(current_user: User = Depends(get_current_user)):
    """Get the Gmail OAuth2 authorization URL to redirect the user to."""
    return {"auth_url": get_auth_url()}


@app.get("/api/gmail/callback")
def gmail_callback(code: str, db: Session = Depends(get_db)):
    """
    Gmail OAuth2 callback — called by Google after user grants access.
    NOTE: In production, use a state parameter to map the callback to the user.
    """
    try:
        token_data = exchange_code_for_token(code)
        # For MVP: find user by their Gmail address
        # In production, use state parameter with session
        service = build_gmail_service(json.dumps(token_data))
        profile = service.users().getProfile(userId="me").execute()
        gmail_address = profile.get("emailAddress", "")

        user = db.query(User).filter(User.email == gmail_address).first()
        if not user:
            # Auto-create user if they log in via Gmail for the first time
            user = User(
                email=gmail_address,
                password_hash=hash_password("gmail-oauth-user"),
                gmail_token=json.dumps(token_data),
            )
            db.add(user)
        else:
            user.gmail_token = json.dumps(token_data)
        db.commit()

        return {"message": f"Gmail connected for {gmail_address}", "email": gmail_address}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth error: {str(e)}")


@app.get("/api/gmail/status")
def gmail_status(current_user: User = Depends(get_current_user)):
    """Check if Gmail is connected for the current user."""
    return {
        "connected": current_user.gmail_token is not None,
        "email": current_user.email,
    }


# ============================================================
# Email / Lead Routes
# ============================================================

@app.post("/api/gmail/fetch-emails")
async def fetch_emails(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger email fetch and lead detection."""
    if not current_user.gmail_token:
        raise HTTPException(status_code=400, detail="Gmail not connected")

    await run_followup_check()
    return {"message": "Email fetch triggered successfully"}


@app.get("/api/leads", response_model=List[LeadResponse])
def get_leads(
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all leads for the current user."""
    query = db.query(Lead).filter(Lead.user_id == current_user.id)
    if status:
        query = query.filter(Lead.status == status)
    leads = query.order_by(Lead.date_received.desc()).offset(skip).limit(limit).all()

    result = []
    for lead in leads:
        followup_count = db.query(FollowUp).filter(
            FollowUp.lead_id == lead.id,
            FollowUp.status == "sent",
        ).count()
        lead_dict = {
            "id": lead.id,
            "from_email": lead.from_email,
            "from_name": lead.from_name,
            "subject": lead.subject,
            "status": lead.status,
            "date_received": lead.date_received,
            "has_replied": lead.has_replied,
            "followup_count": followup_count,
        }
        result.append(lead_dict)
    return result


@app.get("/api/leads/{lead_id}/followups", response_model=List[FollowUpResponse])
def get_lead_followups(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all follow-ups for a specific lead."""
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == current_user.id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead.followups


@app.post("/api/leads/{lead_id}/followup/trigger")
async def trigger_followup_manually(
    lead_id: int,
    followup_number: int = 1,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger an AI follow-up for a specific lead."""
    if not current_user.gmail_token:
        raise HTTPException(status_code=400, detail="Gmail not connected")

    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == current_user.id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Generate AI email
    body = generate_followup_email(
        followup_number=followup_number,
        original_subject=lead.subject,
        original_message=lead.message,
        lead_name=lead.from_name,
    )

    # Send it
    from gmail_service import send_reply_in_thread
    service = build_gmail_service(current_user.gmail_token)
    sent_id = send_reply_in_thread(
        service=service,
        thread_id=lead.thread_id,
        to_email=lead.from_email,
        subject=lead.subject,
        body=body,
        original_message_id=lead.message_id,
    )

    if sent_id:
        # Record the follow-up
        followup = FollowUp(
            lead_id=lead.id,
            followup_number=followup_number,
            scheduled_date=datetime.utcnow(),
            sent_date=datetime.utcnow(),
            status="sent",
            generated_body=body,
        )
        db.add(followup)
        db.commit()
        return {"message": "Follow-up sent successfully", "gmail_id": sent_id}
    else:
        raise HTTPException(status_code=500, detail="Failed to send email")


# ============================================================
# Automation Toggle
# ============================================================

@app.post("/api/automation/toggle")
def toggle_automation(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Enable or disable automatic follow-up sending."""
    current_user.automation_enabled = not current_user.automation_enabled
    db.commit()
    return {
        "automation_enabled": current_user.automation_enabled,
        "message": f"Automation {'enabled' if current_user.automation_enabled else 'disabled'}",
    }


@app.get("/api/automation/status")
def automation_status(current_user: User = Depends(get_current_user)):
    return {"automation_enabled": current_user.automation_enabled}


# ============================================================
# Dashboard Stats
# ============================================================

@app.get("/api/dashboard/stats", response_model=DashboardStats)
def dashboard_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aggregate stats for the dashboard."""
    total = db.query(Lead).filter(Lead.user_id == current_user.id).count()
    new = db.query(Lead).filter(Lead.user_id == current_user.id, Lead.status == "new").count()
    replied = db.query(Lead).filter(Lead.user_id == current_user.id, Lead.status == "replied").count()

    sent = (
        db.query(FollowUp)
        .join(Lead)
        .filter(Lead.user_id == current_user.id, FollowUp.status == "sent")
        .count()
    )
    pending = (
        db.query(FollowUp)
        .join(Lead)
        .filter(Lead.user_id == current_user.id, FollowUp.status == "pending")
        .count()
    )

    return {
        "total_leads": total,
        "new_leads": new,
        "replied_leads": replied,
        "followups_sent": sent,
        "followups_pending": pending,
        "automation_enabled": current_user.automation_enabled,
        "gmail_connected": current_user.gmail_token is not None,
    }


# ============================================================
# Health Check
# ============================================================

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
