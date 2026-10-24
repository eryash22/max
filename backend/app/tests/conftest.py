import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main
from app import models  # noqa: F401 registers tables on Base.metadata
from app.database import Base, get_db
from app.main import app
from app.models.identity import Role, User, UserRole
from app.security.password import hash_password
from app.services.rate_limiter import reset_for_tests as reset_login_rate_limiter
from app.services.seed_service import ensure_roles_and_permissions
from app.services.service_visit_service import ensure_default_service_visit_types

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "SuperSecretPass123!"


@pytest.fixture()
def db_session():
    """Fresh in-memory SQLite database per test.

    SQLite is used only for the automated test suite, per the Phase 1 completion
    standard's requirement for a test strategy that does not need a live
    PostgreSQL instance; the application itself still targets PostgreSQL only.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session, monkeypatch):
    """TestClient(app) fires FastAPI's startup event (app.main.startup()), which
    seeds roles/permissions/service-visit-types through its own `SessionLocal()` —
    independently of the `get_db` dependency override below. `app.main.startup()`
    resolves `SessionLocal` from `app.main`'s module globals, so without this
    monkeypatch it would open a real connection to the configured PostgreSQL
    `DATABASE_URL` on every test that uses this fixture (see test_backend_test_isolation.py).
    Binding it to the same in-memory engine as `db_session` keeps startup seeding
    exercised, but against the isolated test database only.
    """
    test_session_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False)
    monkeypatch.setattr(main, "SessionLocal", test_session_factory)
    # The login rate limiter (services/rate_limiter.py) keeps its counters in a
    # module-level dict so they survive across requests within one running
    # process — exactly what makes it work in production, but it means the
    # counters would otherwise leak between tests too (TestClient's default
    # client IP is the same constant for every test), eventually tripping 429s
    # on unrelated tests purely from cumulative login calls across the suite.
    reset_login_rate_limiter()

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    ensure_roles_and_permissions(db_session)
    ensure_default_service_visit_types(db_session)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def create_user(db_session, email: str, role_code: str, password: str = "Password123!") -> User:
    role = db_session.query(Role).filter(Role.code == role_code).first()
    assert role is not None, f"role {role_code} must be seeded first"
    user = User(email=email, name=email.split("@")[0], hashed_password=hash_password(password), is_active=True)
    db_session.add(user)
    db_session.flush()
    db_session.add(UserRole(user_id=user.id, role_id=role.id))
    db_session.commit()
    return user


def auth_headers(client: TestClient, email: str, password: str) -> dict:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_client(client, db_session):
    create_user(db_session, ADMIN_EMAIL, "SUPER_ADMIN", ADMIN_PASSWORD)
    headers = auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    client.headers.update(headers)
    return client
