"""
Test base — verifica che l'API risponda correttamente.
Uso: pytest tests/
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock


@pytest.fixture
def client():
    """Test client con DB pool mockato."""
    with patch("app.database.get_db_pool", new_callable=AsyncMock):
        from app.main import app
        with TestClient(app) as c:
            yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["message"] == "UTOPIA API"


def test_docs_available_in_dev(client):
    resp = client.get("/docs")
    assert resp.status_code == 200
