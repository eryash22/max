"""Security hardening pass — small, cross-cutting fixes that don't belong to any one
domain router: the CORS wildcard-with-credentials startup guard and the system
health endpoint's error-leakage fix.
"""
from app.config import Settings
from app.database import get_db
from app.main import app


def test_cors_origins_rejects_wildcard():
    settings = Settings(database_url="sqlite://", secret_key="test-secret", cors_origins="*")
    try:
        settings.cors_origin_list
        assert False, "expected ValueError for a wildcard CORS origin"
    except ValueError as exc:
        assert "CORS_ORIGINS" in str(exc)


def test_cors_origins_rejects_wildcard_mixed_with_real_origins():
    settings = Settings(
        database_url="sqlite://", secret_key="test-secret",
        cors_origins="http://localhost:3000,*",
    )
    try:
        settings.cors_origin_list
        assert False, "expected ValueError for a wildcard CORS origin"
    except ValueError:
        pass


def test_cors_origins_accepts_explicit_origin_list():
    settings = Settings(
        database_url="sqlite://", secret_key="test-secret",
        cors_origins="http://localhost:3000,https://app.example.com",
    )
    assert settings.cors_origin_list == ["http://localhost:3000", "https://app.example.com"]


def test_system_health_never_leaks_raw_exception_text(client, db_session, monkeypatch):
    """GET /api/v1/system/health has no auth dependency (infra/load-balancer health
    checks can't carry a bearer token) — a raw DB exception string can contain
    connection-string fragments (host/port/credentials), so only the exception class
    name may be exposed, never str(exc)."""

    class _FakeSession:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("connection to server at postgresql://max_biomedical:s3cr3t@10.0.0.5:5432 failed")

    def _override():
        yield _FakeSession()

    app.dependency_overrides[get_db] = _override
    try:
        response = client.get("/api/v1/system/health")
    finally:
        app.dependency_overrides[get_db] = lambda: (yield db_session)

    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "unreachable"
    assert body["database_detail"] == "RuntimeError"
    assert "s3cr3t" not in str(body)
    assert "10.0.0.5" not in str(body)
