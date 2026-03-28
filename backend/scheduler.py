# scheduler.py — Background task: checks and sends scheduled follow-ups
import json
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from database import SessionLocal, Lead, FollowUp, User
from gmail_service import build_gmail_service, send_reply_in_thread, has_user_replied
from ai_service import generate_followup_email
from config import settings

scheduler = AsyncIOScheduler()


def schedule_followups_for_lead(lead: Lead, db: Session):
    """
    Create FollowUp records for a newly detected lead.
    Day 3, Day 5, Day 7 follow-ups are scheduled from date_received.
    """
    schedule_days = [
        (1, settings.FOLLOWUP_DAY_1),
        (2, settings.FOLLOWUP_DAY_2),
        (3, settings.FOLLOWUP_DAY_3),
    ]
    for followup_number, day_offset in schedule_days:
        # Don't create duplicates
        existing = db.query(FollowUp).filter(
            FollowUp.lead_id == lead.id,
            FollowUp.followup_number == followup_number,
        ).first()
        if not existing:
            followup = FollowUp(
                lead_id=lead.id,
                followup_number=followup_number,
                scheduled_date=lead.date_received + timedelta(days=day_offset),
                status="pending",
            )
            db.add(followup)
    db.commit()


async def run_followup_check():
    """
    Main scheduler job — runs every 30 minutes.
    Checks for:
    1. New emails to turn into leads
    2. Due follow-ups to send
    3. Threads where customer has replied (cancel remaining follow-ups)
    """
    print(f"[Scheduler] Running follow-up check at {datetime.utcnow()}")
    db = SessionLocal()
    try:
        # Get all users with automation enabled and Gmail connected
        users = db.query(User).filter(
            User.automation_enabled == True,
            User.gmail_token != None,
        ).all()

        for user in users:
            try:
                await process_user_followups(user, db)
            except Exception as e:
                print(f"[Scheduler] Error processing user {user.email}: {e}")
    finally:
        db.close()


async def process_user_followups(user: User, db: Session):
    """Process all follow-up tasks for a single user."""
    from gmail_service import fetch_recent_emails, is_inquiry_email

    service = build_gmail_service(user.gmail_token)
    user_email = user.email

    # --- Step 1: Fetch new emails and detect inquiries ---
    emails = fetch_recent_emails(service, max_results=20)
    for email_data in emails:
        # Skip if already stored
        existing = db.query(Lead).filter(
            Lead.message_id == email_data["message_id"],
            Lead.user_id == user.id,
        ).first()
        if existing:
            continue

        # Check if it looks like an inquiry
        if is_inquiry_email(email_data["subject"], email_data["body"]):
            lead = Lead(
                user_id=user.id,
                from_email=email_data["from_email"],
                from_name=email_data["from_name"],
                subject=email_data["subject"],
                message=email_data["body"],
                thread_id=email_data["thread_id"],
                message_id=email_data["message_id"],
                status="new",
            )
            db.add(lead)
            db.flush()  # Get lead.id without committing
            schedule_followups_for_lead(lead, db)
            print(f"[Scheduler] New lead from {email_data['from_email']}")

    db.commit()

    # --- Step 2: Check if any leads have replied ---
    active_leads = db.query(Lead).filter(
        Lead.user_id == user.id,
        Lead.has_replied == False,
        Lead.status == "new",
    ).all()

    for lead in active_leads:
        if lead.thread_id and has_user_replied(service, lead.thread_id, user_email):
            # Mark lead as replied — skip all remaining follow-ups
            lead.has_replied = True
            lead.status = "replied"
            # Cancel pending follow-ups
            db.query(FollowUp).filter(
                FollowUp.lead_id == lead.id,
                FollowUp.status == "pending",
            ).update({"status": "skipped"})
            print(f"[Scheduler] Lead {lead.id} replied — follow-ups cancelled")

    db.commit()

    # --- Step 3: Send due follow-ups ---
    now = datetime.utcnow()
    due_followups = (
        db.query(FollowUp)
        .join(Lead)
        .filter(
            Lead.user_id == user.id,
            Lead.has_replied == False,
            FollowUp.status == "pending",
            FollowUp.scheduled_date <= now,
        )
        .all()
    )

    for followup in due_followups:
        lead = followup.lead
        try:
            # Generate AI email if not already generated
            if not followup.generated_body:
                followup.generated_body = generate_followup_email(
                    followup_number=followup.followup_number,
                    original_subject=lead.subject,
                    original_message=lead.message,
                    lead_name=lead.from_name,
                )

            # Send the follow-up in the same Gmail thread
            sent_id = send_reply_in_thread(
                service=service,
                thread_id=lead.thread_id,
                to_email=lead.from_email,
                subject=lead.subject,
                body=followup.generated_body,
                original_message_id=lead.message_id,
            )

            if sent_id:
                followup.status = "sent"
                followup.sent_date = now
                print(f"[Scheduler] Sent followup #{followup.followup_number} to {lead.from_email}")
            else:
                followup.status = "failed"
                followup.error_message = "Gmail API returned no message ID"

        except Exception as e:
            followup.status = "failed"
            followup.error_message = str(e)
            print(f"[Scheduler] Failed followup {followup.id}: {e}")

    db.commit()


def start_scheduler():
    """Start the APScheduler — runs every 30 minutes."""
    scheduler.add_job(
        run_followup_check,
        trigger="interval",
        minutes=30,
        id="followup_check",
        replace_existing=True,
    )
    scheduler.start()
    print("[Scheduler] Started — checking every 30 minutes")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
