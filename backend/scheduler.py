# scheduler.py — Background scheduler (APScheduler).
# Runs every 30 minutes. Skips gracefully if Gmail is not connected.

from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from config import settings
from database import SessionLocal, Lead, FollowUp, User

scheduler = AsyncIOScheduler()


def schedule_followups_for_lead(lead: Lead, db: Session):
    """Create the three follow-up records for a newly detected lead."""
    plan = [
        (1, settings.FOLLOWUP_DAY_1),
        (2, settings.FOLLOWUP_DAY_2),
        (3, settings.FOLLOWUP_DAY_3),
    ]
    for number, day_offset in plan:
        exists = db.query(FollowUp).filter(
            FollowUp.lead_id == lead.id,
            FollowUp.followup_number == number,
        ).first()
        if not exists:
            db.add(FollowUp(
                lead_id=lead.id,
                followup_number=number,
                scheduled_date=lead.date_received + timedelta(days=day_offset),
                status="pending",
            ))
    db.commit()


async def run_followup_check():
    """Main job — detects leads, checks replies, sends due follow-ups."""
    if not settings.gmail_configured:
        print("[Scheduler] Skipping — Gmail not configured")
        return

    print(f"[Scheduler] Check at {datetime.utcnow().isoformat()}")
    db = SessionLocal()
    try:
        users = db.query(User).filter(
            User.automation_enabled == True,
            User.gmail_token.isnot(None),
        ).all()
        for user in users:
            try:
                await _process_user(user, db)
            except Exception as e:
                print(f"[Scheduler] Error for {user.email}: {e}")
    finally:
        db.close()


async def _process_user(user: User, db: Session):
    from gmail_service import (
        build_gmail_service, fetch_recent_emails,
        is_inquiry_email, send_reply_in_thread, has_user_replied,
    )
    from ai_service import generate_followup_email

    service = build_gmail_service(user.gmail_token)

    # Step 1: Detect new inquiry emails
    for email in fetch_recent_emails(service, max_results=20):
        if db.query(Lead).filter(
            Lead.message_id == email["message_id"],
            Lead.user_id == user.id,
        ).first():
            continue
        if is_inquiry_email(email["subject"], email["body"]):
            lead = Lead(
                user_id=user.id,
                from_email=email["from_email"],
                from_name=email["from_name"],
                subject=email["subject"],
                message=email["body"],
                thread_id=email["thread_id"],
                message_id=email["message_id"],
            )
            db.add(lead)
            db.flush()
            schedule_followups_for_lead(lead, db)
            print(f"[Scheduler] New lead: {email['from_email']}")
    db.commit()

    # Step 2: Check for replies → cancel pending follow-ups
    for lead in db.query(Lead).filter(
        Lead.user_id == user.id, Lead.has_replied == False, Lead.status == "new"
    ).all():
        if lead.thread_id and has_user_replied(service, lead.thread_id, user.email):
            lead.has_replied = True
            lead.status = "replied"
            db.query(FollowUp).filter(
                FollowUp.lead_id == lead.id, FollowUp.status == "pending"
            ).update({"status": "skipped"})
    db.commit()

    # Step 3: Send due follow-ups
    now = datetime.utcnow()
    for fu in (
        db.query(FollowUp).join(Lead)
        .filter(
            Lead.user_id == user.id,
            Lead.has_replied == False,
            FollowUp.status == "pending",
            FollowUp.scheduled_date <= now,
        ).all()
    ):
        lead = fu.lead
        try:
            if not fu.generated_body:
                fu.generated_body = generate_followup_email(
                    fu.followup_number, lead.subject, lead.message, lead.from_name
                )
            sent_id = send_reply_in_thread(
                service, lead.thread_id, lead.from_email,
                lead.subject, fu.generated_body, lead.message_id,
            )
            fu.status    = "sent" if sent_id else "failed"
            fu.sent_date = now if sent_id else None
            if not sent_id:
                fu.error_message = "Gmail send returned no ID"
        except Exception as e:
            fu.status        = "failed"
            fu.error_message = str(e)
    db.commit()


def start_scheduler():
    scheduler.add_job(
        run_followup_check,
        trigger="interval",
        minutes=30,
        id="followup_check",
        replace_existing=True,
    )
    scheduler.start()
    print("[Scheduler] Started — checks every 30 min")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
