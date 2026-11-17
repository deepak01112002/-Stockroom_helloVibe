from __future__ import annotations

from app.api.routes.health import get_db_session
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError


class HealthySession:
    def execute(self, statement: object) -> None:
        assert str(statement) == str(text("SELECT 1"))


class UnhealthySession:
    def execute(self, statement: object) -> None:
        raise OperationalError("SELECT 1", {}, RuntimeError("database down"))


def test_health_reports_database_status() -> None:
    app.dependency_overrides[get_db_session] = lambda: HealthySession()
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_returns_503_when_database_is_down() -> None:
    app.dependency_overrides[get_db_session] = lambda: UnhealthySession()
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
