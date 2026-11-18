from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.schemas.payments import PaymentWebhookRequest, PaymentWebhookResponse
from app.services.inventory import InventoryError
from app.services.payments import PaymentError, PaymentService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
DatabaseSession = Annotated[Session, Depends(get_db_session)]
ApplicationSettings = Annotated[Settings, Depends(get_settings)]
WebhookSignature = Annotated[str, Header(alias="X-Webhook-Signature", min_length=1)]


@router.post("/payments", response_model=PaymentWebhookResponse)
async def receive_payment_webhook(
    request: Request,
    session: DatabaseSession,
    settings: ApplicationSettings,
    signature: WebhookSignature,
) -> PaymentWebhookResponse:
    """Verify and atomically process a provider payment event.

    The provider signs the exact JSON bytes with HMAC-SHA256 and sends the
    value as ``sha256=<hex digest>`` in ``X-Webhook-Signature``. The event is
    either ``payment.succeeded`` or ``payment.failed``.
    """

    secret = settings.payment_webhook_secret
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "webhook_not_configured",
                "message": "webhook secret is not configured",
            },
        )

    raw_body = await request.body()
    expected_signature = "sha256=" + hmac.new(
        secret.get_secret_value().encode(), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "invalid_webhook_signature",
                "message": "signature verification failed",
            },
        )

    try:
        payload = PaymentWebhookRequest.model_validate_json(raw_body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors(),
        ) from exc

    try:
        result = PaymentService(session, settings.hold_ttl_minutes).process_event(
            provider_event_id=payload.provider_event_id,
            hold_id=payload.hold_id,
            outcome=payload.outcome,
            raw_payload=raw_body,
        )
    except InventoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except PaymentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

    return PaymentWebhookResponse(
        event=result.event,
        result=result.result,
        duplicate=result.duplicate,
        order_id=result.order.id if result.order is not None else None,
    )
