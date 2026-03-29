# main.py — FollowFlow FastAPI Backend
# ─────────────────────────────────────────────────────────────
# Start (local):  uvicorn main:app --reload --port 8000
# Start (Render): uvicorn main:app --host 0.0.0.0 --port $PORT
# ─────────────────────────────────────────────────────────────

import json
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session

# ── Internal modules ──────────────────────────────────────────
from config import settings
from database import init_db, get_db, User, Lead, FollowUp, Payment
from auth import hash_password, verify_password, create_access_token, get_current_user

# ── App setup ─────────────────────────────────────────────────
app = FastAPI(
    title="FollowFlow API",
    description="AI-powered Gmail follow-up automation with PayPal billing",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # Restrict to your domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────
# LIFECYCLE
# ─────────────────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    """Initialize DB tables and start the background scheduler."""
    init_db()
    print("[App] Database tables ready")

    # Scheduler import is deferred so a bad APScheduler install
    # doesn't crash the whole app on startup
    try:
        from scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        print(f"[App] Scheduler failed to start (non-fatal): {e}")

    print(f"[App] Gmail configured: {settings.gmail_configured}")
    print(f"[App] OpenAI configured: {settings.openai_configured}")
    print(f"[App] PayPal configured: {settings.paypal_configured}")
    print(f"[App] PayPal mode: {settings.PAYPAL_MODE}")


@app.on_event("shutdown")
async def on_shutdown():
    try:
        from scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# PYDANTIC SCHEMAS
# ─────────────────────────────────────────────────────────────

class UserRegister(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class LeadOut(BaseModel):
    id: int
    from_email: str
    from_name: Optional[str] = None
    subject: Optional[str] = None
    status: str
    date_received: datetime
    has_replied: bool
    followup_count: int = 0
    class Config:
        from_attributes = True

class FollowUpOut(BaseModel):
    id: int
    lead_id: int
    followup_number: int
    scheduled_date: datetime
    sent_date: Optional[datetime] = None
    status: str
    generated_body: Optional[str] = None
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
    plan: str
    sub_status: str

class PayPalOrderRequest(BaseModel):
    plan: str                    # "pro" or "business"
    return_url: str              # Where PayPal redirects after approval
    cancel_url: str              # Where PayPal redirects on cancel

class PayPalCaptureRequest(BaseModel):
    order_id: str
    plan: str

class PayPalSubscriptionRequest(BaseModel):
    plan: str
    return_url: str
    cancel_url: str

class PayPalWebhookEvent(BaseModel):
    event_type: str
    resource: dict


# ─────────────────────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────────────────────

@app.get("/")
def root():
    """Health check — confirms the API is running."""
    return {
        "message": "FollowFlow API is running ✅",
        "version": "1.0.0",
        "features": {
            "gmail":   settings.gmail_configured,
            "openai":  settings.openai_configured,
            "paypal":  settings.paypal_configured,
        },
    }


@app.get("/leads")
def demo_leads():
    """
    Public demo endpoint — returns dummy lead data.
    Useful for testing the frontend before signing in.
    """
    return [
        {"id": 1, "from_email": "sarah@example.com", "from_name": "Sarah Miller",
         "subject": "Pricing inquiry", "status": "new",
         "date_received": "2025-06-01T10:30:00", "has_replied": False, "followup_count": 1},
        {"id": 2, "from_email": "john@corp.io", "from_name": "John Doe",
         "subject": "Are these plans still available?", "status": "replied",
         "date_received": "2025-05-30T08:15:00", "has_replied": True, "followup_count": 2},
        {"id": 3, "from_email": "emma@startup.co", "from_name": "Emma K.",
         "subject": "Demo request — details please", "status": "new",
         "date_received": "2025-05-28T14:00:00", "has_replied": False, "followup_count": 0},
    ]


# ─────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────

@app.post("/api/auth/register", response_model=TokenResponse)
def register(data: UserRegister, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(400, "Email already registered")
    user = User(email=data.email, password_hash=hash_password(data.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"access_token": create_access_token({"sub": str(user.id)})}


@app.post("/api/auth/login", response_model=TokenResponse)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    return {"access_token": create_access_token({"sub": str(user.id)})}


# ─────────────────────────────────────────────────────────────
# GMAIL
# ─────────────────────────────────────────────────────────────

@app.get("/api/gmail/auth-url")
def gmail_auth_url(current_user: User = Depends(get_current_user)):
    if not settings.gmail_configured:
        raise HTTPException(503, "Gmail is not configured on this server")
    from gmail_service import get_auth_url
    return {"auth_url": get_auth_url()}


@app.get("/api/gmail/callback")
def gmail_callback(code: str, db: Session = Depends(get_db)):
    if not settings.gmail_configured:
        raise HTTPException(503, "Gmail is not configured on this server")
    try:
        from gmail_service import exchange_code_for_token, build_gmail_service
        token_data    = exchange_code_for_token(code)
        service       = build_gmail_service(json.dumps(token_data))
        profile       = service.users().getProfile(userId="me").execute()
        gmail_address = profile.get("emailAddress", "")
        user = db.query(User).filter(User.email == gmail_address).first()
        if not user:
            user = User(
                email=gmail_address,
                password_hash=hash_password("gmail-oauth"),
                gmail_token=json.dumps(token_data),
            )
            db.add(user)
        else:
            user.gmail_token = json.dumps(token_data)
        db.commit()
        return {"message": f"Gmail connected for {gmail_address}"}
    except Exception as e:
        raise HTTPException(400, f"OAuth error: {e}")


@app.get("/api/gmail/status")
def gmail_status(current_user: User = Depends(get_current_user)):
    return {
        "connected": current_user.gmail_token is not None,
        "email": current_user.email,
        "configured": settings.gmail_configured,
    }


# ─────────────────────────────────────────────────────────────
# EMAIL SYNC & LEADS
# ─────────────────────────────────────────────────────────────

@app.post("/api/gmail/fetch-emails")
async def fetch_emails(current_user: User = Depends(get_current_user)):
    if not settings.gmail_configured:
        raise HTTPException(503, "Gmail is not configured")
    if not current_user.gmail_token:
        raise HTTPException(400, "Gmail not connected")
    from scheduler import run_followup_check
    await run_followup_check()
    return {"message": "Email fetch triggered"}


@app.get("/api/leads", response_model=List[LeadOut])
def get_leads(
    skip: int = 0, limit: int = 50, status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Lead).filter(Lead.user_id == current_user.id)
    if status:
        q = q.filter(Lead.status == status)
    leads = q.order_by(Lead.date_received.desc()).offset(skip).limit(limit).all()
    result = []
    for lead in leads:
        count = db.query(FollowUp).filter(
            FollowUp.lead_id == lead.id, FollowUp.status == "sent"
        ).count()
        result.append({**{c.name: getattr(lead, c.name) for c in lead.__table__.columns},
                       "followup_count": count})
    return result


@app.get("/api/leads/{lead_id}/followups", response_model=List[FollowUpOut])
def get_lead_followups(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == current_user.id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    return lead.followups


@app.post("/api/leads/{lead_id}/followup/trigger")
async def trigger_followup(
    lead_id: int, followup_number: int = 1,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.gmail_token:
        raise HTTPException(400, "Gmail not connected")
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.user_id == current_user.id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")

    from ai_service import generate_followup_email
    from gmail_service import build_gmail_service, send_reply_in_thread

    body     = generate_followup_email(followup_number, lead.subject, lead.message, lead.from_name)
    service  = build_gmail_service(current_user.gmail_token)
    sent_id  = send_reply_in_thread(service, lead.thread_id, lead.from_email,
                                    lead.subject, body, lead.message_id)
    if sent_id:
        fu = FollowUp(lead_id=lead.id, followup_number=followup_number,
                      scheduled_date=datetime.utcnow(), sent_date=datetime.utcnow(),
                      status="sent", generated_body=body)
        db.add(fu)
        db.commit()
        return {"message": "Follow-up sent", "gmail_id": sent_id}
    raise HTTPException(500, "Failed to send email via Gmail")


# ─────────────────────────────────────────────────────────────
# AUTOMATION
# ─────────────────────────────────────────────────────────────

@app.post("/api/automation/toggle")
def toggle_automation(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.automation_enabled = not current_user.automation_enabled
    db.commit()
    return {"automation_enabled": current_user.automation_enabled}


@app.get("/api/automation/status")
def automation_status(current_user: User = Depends(get_current_user)):
    return {"automation_enabled": current_user.automation_enabled}


# ─────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────

@app.get("/api/dashboard/stats", response_model=DashboardStats)
def dashboard_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    uid     = current_user.id
    total   = db.query(Lead).filter(Lead.user_id == uid).count()
    new     = db.query(Lead).filter(Lead.user_id == uid, Lead.status == "new").count()
    replied = db.query(Lead).filter(Lead.user_id == uid, Lead.status == "replied").count()
    sent    = (db.query(FollowUp).join(Lead)
               .filter(Lead.user_id == uid, FollowUp.status == "sent").count())
    pending = (db.query(FollowUp).join(Lead)
               .filter(Lead.user_id == uid, FollowUp.status == "pending").count())
    return {
        "total_leads": total, "new_leads": new, "replied_leads": replied,
        "followups_sent": sent, "followups_pending": pending,
        "automation_enabled": current_user.automation_enabled,
        "gmail_connected": current_user.gmail_token is not None,
        "plan": current_user.plan or "free",
        "sub_status": current_user.sub_status or "inactive",
    }


# ─────────────────────────────────────────────────────────────
# PAYPAL — ONE-TIME ORDERS
# ─────────────────────────────────────────────────────────────

@app.get("/api/billing/plans")
def get_plans():
    """Return available pricing plans. Always works, even without PayPal."""
    return {
        "plans": [
            {"id": "basic",    "name": "Basic",    "price": "0",  "period": "forever",
             "features": ["1 Gmail account", "30 leads/month", "Day 3 follow-up only"]},
            {"id": "pro",      "name": "Pro",      "price": "29", "period": "month",
             "features": ["Unlimited leads", "Full 3-stage AI follow-ups",
                          "Auto-stop on reply", "Analytics"]},
            {"id": "business", "name": "Business", "price": "79", "period": "month",
             "features": ["5 Gmail accounts", "Unlimited leads", "Team dashboard",
                          "Webhook integrations", "Priority support"]},
        ],
        "paypal_configured": settings.paypal_configured,
        "paypal_mode": settings.PAYPAL_MODE,
        # Frontend uses this Client ID in the PayPal JS SDK
        "paypal_client_id": settings.PAYPAL_CLIENT_ID or "",
    }


@app.post("/api/billing/create-order")
async def create_order(
    data: PayPalOrderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not settings.paypal_configured:
        raise HTTPException(503, "PayPal is not configured on this server")

    try:
        from paypal_service import create_order as pp_create_order
    except Exception:
        raise HTTPException(503, "PayPal service unavailable")

    result = await pp_create_order(data.plan, data.return_url, data.cancel_url)

    if not result:
        raise HTTPException(502, "Failed to create PayPal order")

    payment = Payment(
        user_id=current_user.id,
        paypal_order_id=result["order_id"],
        plan=data.plan,
        amount={"pro": "29.00", "business": "79.00"}.get(data.plan, "0.00"),
        status="pending",
    )
    db.add(payment)
    db.commit()

    return {
        "order_id": result["order_id"],
        "approval_url": result["approval_url"],
    }

@app.post("/api/billing/capture-order")
async def capture_order(
    data: PayPalCaptureRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Capture a PayPal order after the user approves it.
    Call this from the frontend after PayPal redirects back.
    """
    if not settings.paypal_configured:
        raise HTTPException(503, "PayPal is not configured on this server")

    # ✅ FIXED INDENTATION
    try:
        from paypal_service import capture_order as pp_capture
    except Exception:
        raise HTTPException(503, "PayPal service not available")

    result = await pp_capture(data.order_id)

    if not result or result.get("status") != "COMPLETED":
        raise HTTPException(402, "Payment was not completed")

    # Update payment record
    payment = db.query(Payment).filter(
        Payment.paypal_order_id == data.order_id,
        Payment.user_id == current_user.id,
    ).first()

    if payment:
        payment.paypal_capture_id = result.get("capture_id")
        payment.status = "completed"

    # Upgrade user's plan
    current_user.plan = data.plan
    current_user.sub_status = "active"

    db.commit()

    return {
        "message": f"Payment successful! Plan upgraded to {data.plan}.",
        "capture_id": result.get("capture_id"),
        "amount": result.get("amount"),
    }
    
# ─────────────────────────────────────────────────────────────
# PAYPAL — SUBSCRIPTIONS (recurring billing)
# ─────────────────────────────────────────────────────────────

@app.post("/api/billing/create-subscription")
async def create_subscription(
    data: PayPalSubscriptionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a PayPal recurring subscription and return the approval URL."""
    if not settings.paypal_configured:
        raise HTTPException(503, "PayPal is not configured on this server")

    from paypal_service import create_subscription as pp_sub

    result = await pp_sub(data.plan, data.return_url, data.cancel_url)
    if not result:
        raise HTTPException(502, "Failed to create PayPal subscription. "
                                 "Check that PAYPAL_PLAN_IDS are set in paypal_service.py")

    # Record pending subscription payment
    payment = Payment(
        user_id=current_user.id,
        paypal_sub_id=result["subscription_id"],
        plan=data.plan,
        status="pending",
    )
    db.add(payment)
    db.commit()

    return {
        "subscription_id": result["subscription_id"],
        "approval_url": result["approval_url"],
    }


@app.post("/api/billing/activate-subscription")
async def activate_subscription(
    subscription_id: str,
    plan: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Called after PayPal redirects back with subscription_id.
    Activates the plan on the user's account.
    """
    current_user.plan         = plan
    current_user.paypal_sub_id = subscription_id
    current_user.sub_status   = "active"
    current_user.sub_expires_at = None  # Recurring — no fixed expiry

    # Update payment record
    payment = db.query(Payment).filter(
        Payment.paypal_sub_id == subscription_id,
        Payment.user_id == current_user.id,
    ).first()
    if payment:
        payment.status = "active"

    db.commit()
    return {"message": f"Subscription activated for plan: {plan}"}


@app.post("/api/billing/cancel-subscription")
async def cancel_subscription(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel the user's active PayPal subscription."""
    if not current_user.paypal_sub_id:
        raise HTTPException(400, "No active subscription found")

    from paypal_service import cancel_subscription as pp_cancel
    ok = await pp_cancel(current_user.paypal_sub_id)
    if not ok:
        raise HTTPException(502, "Failed to cancel subscription with PayPal")

    current_user.sub_status = "cancelled"
    current_user.plan       = "free"
    db.commit()
    return {"message": "Subscription cancelled. You will retain access until the end of the billing period."}


@app.get("/api/billing/my-plan")
def my_plan(current_user: User = Depends(get_current_user)):
    """Return the current user's plan and billing status."""
    return {
        "plan":       current_user.plan or "free",
        "sub_status": current_user.sub_status or "inactive",
        "sub_id":     current_user.paypal_sub_id,
    }


# ─────────────────────────────────────────────────────────────
# PAYPAL — WEBHOOK  (server-to-server events)
# ─────────────────────────────────────────────────────────────

@app.post("/api/billing/webhook")
async def paypal_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receive PayPal webhook events.
    Configure this URL in your PayPal Developer Dashboard → Webhooks.
    URL: https://your-backend.onrender.com/api/billing/webhook

    Events handled:
      - BILLING.SUBSCRIPTION.ACTIVATED
      - BILLING.SUBSCRIPTION.CANCELLED
      - BILLING.SUBSCRIPTION.EXPIRED
      - PAYMENT.CAPTURE.COMPLETED
      - PAYMENT.CAPTURE.DENIED
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid webhook payload")

    event_type = body.get("event_type", "")
    resource   = body.get("resource", {})

    print(f"[PayPal Webhook] {event_type}")

    if event_type == "BILLING.SUBSCRIPTION.CANCELLED":
        sub_id = resource.get("id")
        if sub_id:
            user = db.query(User).filter(User.paypal_sub_id == sub_id).first()
            if user:
                user.sub_status = "cancelled"
                user.plan       = "free"
                db.commit()
                print(f"[Webhook] Subscription cancelled for {user.email}")

    elif event_type == "BILLING.SUBSCRIPTION.EXPIRED":
        sub_id = resource.get("id")
        if sub_id:
            user = db.query(User).filter(User.paypal_sub_id == sub_id).first()
            if user:
                user.sub_status = "expired"
                user.plan       = "free"
                db.commit()

    elif event_type == "PAYMENT.CAPTURE.COMPLETED":
        order_id = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
        if order_id:
            payment = db.query(Payment).filter(Payment.paypal_order_id == order_id).first()
            if payment:
                payment.status            = "completed"
                payment.paypal_capture_id = resource.get("id")
                db.commit()

    elif event_type == "PAYMENT.CAPTURE.DENIED":
        order_id = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
        if order_id:
            payment = db.query(Payment).filter(Payment.paypal_order_id == order_id).first()
            if payment:
                payment.status = "failed"
                db.commit()

    # Always return 200 — PayPal will retry if it gets anything else
    return {"status": "received"}


# ─────────────────────────────────────────────────────────────
# PAYMENT HISTORY
# ─────────────────────────────────────────────────────────────

@app.get("/api/billing/history")
def payment_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the current user's payment history."""
    payments = (
        db.query(Payment)
        .filter(Payment.user_id == current_user.id)
        .order_by(Payment.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id":         p.id,
            "plan":       p.plan,
            "amount":     p.amount,
            "currency":   p.currency,
            "status":     p.status,
            "created_at": p.created_at.isoformat(),
            "order_id":   p.paypal_order_id,
            "sub_id":     p.paypal_sub_id,
        }
        for p in payments
    ]
