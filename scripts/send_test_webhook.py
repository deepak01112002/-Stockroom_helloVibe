"""Send an assignment-compatible, correctly signed payment webhook."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import uuid

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("hold_id")
    parser.add_argument("--event", choices=["payment.succeeded", "payment.failed"], required=True)
    parser.add_argument("--url", default="http://localhost:8000/webhooks/payments")
    args = parser.parse_args()
    secret = os.environ.get("PAYMENT_WEBHOOK_SECRET")
    if not secret:
        raise SystemExit("PAYMENT_WEBHOOK_SECRET must be set")
    payload = json.dumps(
        {
            "provider_event_id": f"manual-test-{uuid.uuid4()}",
            "hold_id": args.hold_id,
            "event": args.event,
        },
        separators=(",", ":"),
    ).encode()
    signature = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    response = httpx.post(args.url, content=payload, headers={"X-Webhook-Signature": signature})
    print(response.status_code, response.text)
    response.raise_for_status()


if __name__ == "__main__":
    main()
