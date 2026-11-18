from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import PaymentEventType, PaymentOutcome


class PaymentWebhookRequest(BaseModel):
    provider_event_id: str = Field(min_length=1, max_length=255)
    hold_id: uuid.UUID
    event: PaymentEventType

    @property
    def outcome(self) -> PaymentOutcome:
        if self.event is PaymentEventType.SUCCEEDED:
            return PaymentOutcome.SUCCEEDED
        return PaymentOutcome.FAILED


class PaymentEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider_event_id: str
    hold_id: uuid.UUID
    outcome: PaymentOutcome
    processing_result: str
    processed_at: datetime


class PaymentWebhookResponse(BaseModel):
    event: PaymentEventResponse
    result: str
    duplicate: bool
    order_id: uuid.UUID | None
