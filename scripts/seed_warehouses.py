from __future__ import annotations

from app.db.session import SessionLocal
from app.services.warehouses import ensure_seed_warehouses


def main() -> None:
    with SessionLocal() as session:
        warehouses = ensure_seed_warehouses(session)
        session.commit()
    print(f"seeded {len(warehouses)} warehouses: {', '.join(sorted(warehouses))}")


if __name__ == "__main__":
    main()
