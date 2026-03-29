# ai_service.py — AI-generated follow-up emails.
# If OPENAI_API_KEY is not set, falls back to static templates silently.
# No crash, no exception — the app just skips the AI call.

from config import settings

# ── Prompt templates ──────────────────────────────────────────
_PROMPTS = {
    1: """You are a friendly sales rep writing a follow-up email (Day 3 — reminder).

Customer's original inquiry:
Subject: {subject}
Message: {message}

Write a warm, concise follow-up (3-4 sentences). Reference their inquiry.
Sound human. No subject line — body only. Tone: friendly, zero pressure.""",

    2: """You are a sales rep writing a value-focused follow-up (Day 5).

Customer's original inquiry:
Subject: {subject}
Message: {message}

Write a follow-up (4-5 sentences). Highlight 1-2 benefits relevant to their question.
Address a likely objection. Soft CTA. No subject line — body only. Tone: professional.""",

    3: """You are a sales rep writing a final follow-up (Day 7 — urgency).

Customer's original inquiry:
Subject: {subject}
Message: {message}

Write a final follow-up (3-4 sentences). Gentle urgency. Make clear it's your last message.
Optional small incentive. No subject line — body only. Tone: warm but urgent.""",
}

# ── Static fallbacks (used when OpenAI is unavailable) ────────
_FALLBACKS = {
    1: (
        "Hi {name},\n\n"
        "I wanted to follow up on your recent inquiry. I'd love to help you "
        "find the right solution — feel free to reply with any questions!\n\n"
        "Looking forward to hearing from you."
    ),
    2: (
        "Hi {name},\n\n"
        "Just checking in to see if you had any questions about your inquiry. "
        "Our solution has helped many customers save time and money. "
        "Happy to walk you through the details — just reply to this email.\n\n"
        "Best regards,"
    ),
    3: (
        "Hi {name},\n\n"
        "This will be my last follow-up so I don't crowd your inbox. "
        "If you're still interested, I can offer a special discount this week. "
        "Otherwise, feel free to reach out any time in the future!\n\n"
        "Take care,"
    ),
}


def generate_followup_email(
    followup_number: int,
    original_subject: str,
    original_message: str,
    lead_name: str = "",
) -> str:
    """
    Generate a personalized follow-up email body.

    Returns AI-generated text if OPENAI_API_KEY is set,
    otherwise returns a static fallback template.
    """
    greeting = lead_name or "there"

    # ── Fast path: no API key → use fallback immediately ──────
    if not settings.openai_configured:
        template = _FALLBACKS.get(followup_number, _FALLBACKS[1])
        return template.format(name=greeting)

    # ── AI path ───────────────────────────────────────────────
    try:
        from openai import OpenAI         # imported lazily to avoid crash if not installed
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        prompt = _PROMPTS.get(followup_number, _PROMPTS[1]).format(
            subject=original_subject or "(no subject)",
            message=(original_message or "(no message)")[:500],
        )
        if lead_name:
            prompt += f"\n\nAddress the customer as '{lead_name}' in the opening."

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert sales copywriter. "
                        "Write concise, human-sounding follow-up emails. "
                        "Return the email body only — no subject line."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=300,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()

    except Exception as exc:
        # Any failure (network, quota, bad key) → use fallback
        print(f"[AI] OpenAI unavailable ({exc}), using fallback template")
        template = _FALLBACKS.get(followup_number, _FALLBACKS[1])
        return template.format(name=greeting)
