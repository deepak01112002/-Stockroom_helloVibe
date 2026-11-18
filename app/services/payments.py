from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import HoldStatus, Order, PaymentEvent, PaymentOutcome
from app.services.inventory import InventoryError, InventoryService


class PaymentError(RuntimeError):
    code = "payment_error"


class PaymentEventConflictError(PaymentError):
    code = "payment_event_conflict"


@dataclass(frozen=True)
class PaymentProcessingResult:
    event: PaymentEvent
    result: str
    duplicate: bool
    order: Order | None


class PaymentService:
    def __init__(self, session: Session, hold_ttl_minutes: int) -> None:
        self._session = session
        self._inventory = InventoryService(session, hold_ttl_minutes)

    def process_event(
        self,
        *,
        provider_event_id: str,
        hold_id: uuid.UUID,
        outcome: PaymentOutcome,
        raw_payload: bytes,
    ) -> PaymentProcessingResult:
        payload_hash = hashlib.sha256(raw_payload).hexdigest()
        try:
            with self._session.begin():
                existing = self._find_event(provider_event_id)
                if existing is not None:
                    self._assert_matching_event(existing, payload_hash)
                    return PaymentProcessingResult(
                        event=existing,
                        result=existing.processing_result,
                        duplicate=True,
                        order=self._order_for_processed_event(existing),
                    )

                event = PaymentEvent(
                    provider_event_id=provider_event_id,
                    hold_id=hold_id,
                    outcome=outcome,
                    payload_hash=payload_hash,
                    processing_result="received",
                )
                self._session.add(event)
                self._session.flush()

                # Every path that can change an existing hold locks Hold first.
                hold = self._inventory._lock_hold(hold_id)
                order: Order | None = None
                if hold.status is HoldStatus.ACTIVE and self._inventory._is_expired(hold):
                    self._inventory._expire_locked_hold(hold)
                    result = "hold_expired"
                elif hold.status is HoldStatus.ACTIVE and outcome is PaymentOutcome.SUCCEEDED:
                    order = self._inventory._confirm_locked_hold(hold)
                    result = "confirmed"
                elif hold.status is HoldStatus.ACTIVE and outcome is PaymentOutcome.FAILED:
                    self._inventory._cancel_locked_hold(hold)
                    result = "cancelled"
                elif hold.status is HoldStatus.CONFIRMED:
                    order = self._order_for_hold(hold.id)
                    if order is None:
                        raise InventoryError("confirmed hold has no order")
                    result = "already_confirmed"
                else:
                    result = f"already_{hold.status.value}"

                event.processing_result = result
                self._session.flush()
                return PaymentProcessingResult(
                    event=event,
                    result=result,
                    duplicate=False,
                    order=order,
                )
        except IntegrityError:
            # A concurrent delivery may win the unique event-ID insert. Its committed
            # transaction is now the authoritative result and can be acknowledged.
            existing = self._find_event(provider_event_id)
            if existing is None:
                raise
            self._assert_matching_event(existing, payload_hash)
            return PaymentProcessingResult(
                event=existing,
                result=existing.processing_result,
                duplicate=True,
                order=self._order_for_processed_event(existing),
            )

    def _find_event(self, provider_event_id: str) -> PaymentEvent | None:
        return self._session.scalar(
            select(PaymentEvent).where(PaymentEvent.provider_event_id == provider_event_id)
        )

    def _order_for_processed_event(self, event: PaymentEvent) -> Order | None:
        if event.processing_result not in {"confirmed", "already_confirmed"}:
            return None
        return self._order_for_hold(event.hold_id)

    def _order_for_hold(self, hold_id: uuid.UUID) -> Order | None:
        return self._session.scalar(select(Order).where(Order.hold_id == hold_id))

    @staticmethod
    def _assert_matching_event(event: PaymentEvent, payload_hash: str) -> None:
        if event.payload_hash != payload_hash:
            raise PaymentEventConflictError(
                "provider event ID was previously used with a different payload"
            )
