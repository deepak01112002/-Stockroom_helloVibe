from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Warehouse

WAREHOUSE_DEFINITIONS = (
    ("warehouse-a", "Warehouse A"),
    ("warehouse-b", "Warehouse B"),
)


def ensure_seed_warehouses(session: Session) -> dict[str, Warehouse]:
    """Create the two fixed warehouses once and return them keyed by stable code."""

    warehouses = {
        warehouse.code: warehouse
        for warehouse in session.scalars(
            select(Warehouse).where(Warehouse.code.in_([code for code, _ in WAREHOUSE_DEFINITIONS]))
        )
    }
    for code, name in WAREHOUSE_DEFINITIONS:
        if code not in warehouses:
            warehouse = Warehouse(code=code, name=name, active=True)
            session.add(warehouse)
            warehouses[code] = warehouse

    session.flush()
    return warehouses
