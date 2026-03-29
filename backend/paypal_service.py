# paypal_service.py — PayPal REST API integration.
# Handles one-time orders and recurring subscriptions.
# Uses httpx (async-compatible, already in requirements).
# Gracefully returns None/error if PayPal is not configured.

import httpx
from typing import Optional
from config import settings


# ── Pricing plans ─────────────────────────────────────────────
# Update these PayPal Plan IDs after creating them in your
# PayPal Developer Dashboard → Subscriptions → Plans.
PLAN_PRICES = {
    "basic":    {"price": "0.00",  "label": "Basic (Free)"},
    "pro":      {"price": "29.00", "label": "Pro"},
    "business": {"price": "79.00", "label": "Business"},
}

# Map plan name → PayPal Subscription Plan ID
# Create these at: https://developer.paypal.com/dashboard/subscriptions
# Then paste the P-XXXX IDs here.
PAYPAL_PLAN_IDS = {
    "pro":      "P-REPLACE-WITH-PRO-PLAN-ID",
    "business": "P-REPLACE-WITH-BUSINESS-PLAN-ID",
}


async def _get_access_token() -> Optional[str]:
    """Exchange client credentials for a PayPal access token."""
    if not settings.paypal_configured:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{settings.paypal_base_url}/v1/oauth2/token",
                auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
                data={"grant_type": "client_credentials"},
                timeout=10,
            )
            r.raise_for_status()
            return r.json().get("access_token")
    except Exception as e:
        print(f"[PayPal] Auth error: {e}")
        return None


async def create_order(plan: str, return_url: str, cancel_url: str) -> Optional[dict]:
    """
    Create a one-time PayPal order for the given plan.
    Returns the order dict (id + approval URL) or None on failure.

    Used for one-time charges (e.g. annual plan).
    """
    price_info = PLAN_PRICES.get(plan)
    if not price_info:
        return None

    token = await _get_access_token()
    if not token:
        return None

    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {
                    "currency_code": "USD",
                    "value": price_info["price"],
                },
                "description": f"FollowFlow {price_info['label']} Plan",
            }
        ],
        "application_context": {
            "return_url": return_url,
            "cancel_url": cancel_url,
            "brand_name": "FollowFlow",
            "user_action": "PAY_NOW",
        },
    }

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{settings.paypal_base_url}/v2/checkout/orders",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            # Extract the approval URL for redirecting the user
            approval_url = next(
                (link["href"] for link in data.get("links", []) if link["rel"] == "approve"),
                None,
            )
            return {"order_id": data["id"], "approval_url": approval_url}
    except Exception as e:
        print(f"[PayPal] create_order error: {e}")
        return None


async def capture_order(order_id: str) -> Optional[dict]:
    """
    Capture a PayPal order after the user approves it.
    Returns capture details (status, capture_id, amount) or None.
    """
    token = await _get_access_token()
    if not token:
        return None

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{settings.paypal_base_url}/v2/checkout/orders/{order_id}/capture",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            # Dig out the capture ID and amount
            capture = (
                data.get("purchase_units", [{}])[0]
                .get("payments", {})
                .get("captures", [{}])[0]
            )
            return {
                "status":     data.get("status"),
                "capture_id": capture.get("id"),
                "amount":     capture.get("amount", {}).get("value"),
                "currency":   capture.get("amount", {}).get("currency_code", "USD"),
            }
    except Exception as e:
        print(f"[PayPal] capture_order error: {e}")
        return None


async def create_subscription(plan: str, return_url: str, cancel_url: str) -> Optional[dict]:
    """
    Create a PayPal recurring subscription for a plan.
    Requires a PayPal Plan ID configured in PAYPAL_PLAN_IDS above.
    Returns {"subscription_id": ..., "approval_url": ...} or None.
    """
    plan_id = PAYPAL_PLAN_IDS.get(plan)
    if not plan_id or plan_id.startswith("P-REPLACE"):
        print(f"[PayPal] No plan ID configured for plan '{plan}'")
        return None

    token = await _get_access_token()
    if not token:
        return None

    payload = {
        "plan_id": plan_id,
        "application_context": {
            "brand_name":        "FollowFlow",
            "return_url":        return_url,
            "cancel_url":        cancel_url,
            "user_action":       "SUBSCRIBE_NOW",
            "shipping_preference": "NO_SHIPPING",
        },
    }

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{settings.paypal_base_url}/v1/billing/subscriptions",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            approval_url = next(
                (link["href"] for link in data.get("links", []) if link["rel"] == "approve"),
                None,
            )
            return {"subscription_id": data["id"], "approval_url": approval_url}
    except Exception as e:
        print(f"[PayPal] create_subscription error: {e}")
        return None


async def cancel_subscription(subscription_id: str, reason: str = "Cancelled by user") -> bool:
    """Cancel an active PayPal subscription. Returns True on success."""
    token = await _get_access_token()
    if not token:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{settings.paypal_base_url}/v1/billing/subscriptions/{subscription_id}/cancel",
                json={"reason": reason},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=15,
            )
            return r.status_code == 204
    except Exception as e:
        print(f"[PayPal] cancel_subscription error: {e}")
        return False


async def get_subscription_details(subscription_id: str) -> Optional[dict]:
    """Fetch current subscription status from PayPal."""
    token = await _get_access_token()
    if not token:
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{settings.paypal_base_url}/v1/billing/subscriptions/{subscription_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        print(f"[PayPal] get_subscription error: {e}")
        return None
