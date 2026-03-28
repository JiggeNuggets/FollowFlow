# ai_service.py — OpenAI-powered follow-up email generation
from openai import OpenAI
from config import settings

client = OpenAI(api_key=settings.OPENAI_API_KEY)

# ---- Prompt Templates for Each Follow-Up Stage ----

FOLLOWUP_PROMPTS = {
    1: """You are a helpful, professional sales representative writing a friendly follow-up email.

The customer sent this inquiry:
Subject: {subject}
Message: {message}

Write a warm, friendly follow-up email (Day 3 reminder). 
Goals:
- Remind them of their inquiry
- Show you're ready to help
- Keep it brief (3-4 sentences)
- Sound human, not robotic
- Do NOT include a subject line, just the body

Tone: Friendly, helpful, not pushy""",

    2: """You are a helpful sales representative writing a value-focused follow-up email.

The customer originally asked about:
Subject: {subject}
Message: {message}

Write a follow-up email (Day 5 — value-based). 
Goals:
- Highlight 1-2 key benefits/value propositions
- Address potential objections briefly
- Include a soft call-to-action
- Keep it to 4-5 sentences
- Do NOT include a subject line, just the body

Tone: Professional, value-focused, helpful""",

    3: """You are a sales representative writing a final follow-up email with a sense of urgency.

The customer originally asked about:
Subject: {subject}
Message: {message}

Write a final follow-up email (Day 7 — urgency/last attempt).
Goals:
- Create gentle urgency (limited availability, special offer, etc.)
- Make it clear this is your last follow-up
- Offer a small incentive if appropriate (discount, free consultation, etc.)
- Keep it concise (3-4 sentences)
- Do NOT include a subject line, just the body

Tone: Warm but urgent, make them feel they might miss out""",
}


def generate_followup_email(
    followup_number: int,
    original_subject: str,
    original_message: str,
    lead_name: str = "",
) -> str:
    """
    Use OpenAI to generate a personalized follow-up email body.

    Args:
        followup_number: 1, 2, or 3 (Day 3, 5, or 7)
        original_subject: Subject of the lead's original email
        original_message: Body of the lead's original email
        lead_name: First name of the lead (optional)

    Returns:
        Generated email body as a string
    """
    prompt_template = FOLLOWUP_PROMPTS.get(followup_number, FOLLOWUP_PROMPTS[1])

    prompt = prompt_template.format(
        subject=original_subject or "(no subject)",
        message=(original_message or "(no message)")[:500],  # Limit context
    )

    # Add name personalization if available
    if lead_name:
        prompt += f"\n\nAddress the customer as '{lead_name}' in the opening line."

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # Fast, cheap, good quality
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert sales copywriter. "
                        "Write personalized, human-sounding follow-up emails. "
                        "Always be concise and genuine. Never use clichés."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=300,
            temperature=0.7,  # Some creativity but not too wild
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        # Fallback template if OpenAI fails
        print(f"OpenAI error: {e}")
        return _fallback_email(followup_number, lead_name)


def _fallback_email(followup_number: int, lead_name: str = "") -> str:
    """Simple fallback email if AI generation fails."""
    greeting = f"Hi {lead_name}," if lead_name else "Hi there,"
    templates = {
        1: (
            f"{greeting}\n\nI wanted to follow up on your recent inquiry. "
            "I'd love to help you find the right solution. "
            "Feel free to reply to this email or schedule a quick call.\n\n"
            "Looking forward to hearing from you!"
        ),
        2: (
            f"{greeting}\n\nJust checking in to see if you had any questions "
            "about your inquiry. Many of our customers find that our solution "
            "saves them significant time and money. "
            "I'm happy to walk you through the details.\n\nBest regards,"
        ),
        3: (
            f"{greeting}\n\nThis is my last follow-up — I don't want to crowd "
            "your inbox! If you're still interested, I'd love to offer you a "
            "special discount if we can connect this week. "
            "Otherwise, feel free to reach out anytime.\n\nTake care!"
        ),
    }
    return templates.get(followup_number, templates[1])
