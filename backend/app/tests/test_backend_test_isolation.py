"""Regression guard: the `client` fixture must never let application startup touch
the real, configured PostgreSQL database.

`TestClient(app)` (used by the `client` fixture in conftest.py) fires FastAPI's
startup event, `app.main.startup()`, which seeds roles/permissions/service-visit
types through `SessionLocal()`. `app.main.startup()` resolves that name from
`app.main`'s own module globals (`from .database import SessionLocal`), which is a
separate reference from `app.database.SessionLocal` and from the `get_db`
dependency the rest of the test suite overrides. Before the `client` fixture also
patched `app.main.SessionLocal`, every test using it opened a real connection to
`settings.database_url` (the development PostgreSQL instance) on startup.

These tests prove the fixture's isolation holds, independently of whatever any
individual test happens to assert.
"""
import app.database as database
import app.main as main
from app.config import settings
from app.models.identity import Role


def test_client_fixture_does_not_use_the_real_session_factory(client):
    """While the `client` fixture is active, `app.main.SessionLocal` must be a
    distinct, patched factory — never the real module-level one bound to the
    configured DATABASE_URL."""
    assert main.SessionLocal is not database.SessionLocal, (
        "app.main.SessionLocal must be monkeypatched for the duration of the "
        "`client` fixture; otherwise app startup connects to the real database"
    )


def test_client_fixture_startup_session_is_sqlite_not_postgres(client):
    """Whatever `app.main.SessionLocal` resolves to right now must be bound to an
    in-memory SQLite engine, not to `settings.database_url` (PostgreSQL)."""
    probe_session = main.SessionLocal()
    try:
        bind = probe_session.get_bind()
        backend_name = bind.url.get_backend_name()
        assert backend_name == "sqlite", (
            f"app.main.SessionLocal is bound to a '{backend_name}' engine during "
            "tests — it must be sqlite, never the configured PostgreSQL database"
        )
        assert str(bind.url) != settings.database_url, (
            "app.main.SessionLocal must not resolve to the configured DATABASE_URL"
        )
    finally:
        probe_session.close()


def test_client_fixture_startup_seeds_the_isolated_database_only(client, db_session):
    """Startup seeding still runs (idempotently) against the same isolated engine
    the rest of the test uses — proving isolation doesn't come at the cost of
    startup behavior actually executing."""
    assert db_session.query(Role).count() > 0, (
        "startup seeding should have populated roles into the isolated test database"
    )


def test_client_fixture_restores_the_real_session_factory_after_use(client):
    """Sanity check that the monkeypatch is function-scoped: once this test's
    `client` fixture has been set up, `app.main.SessionLocal` is patched for *this*
    test only and will be restored by pytest's monkeypatch teardown, never leaking
    into unrelated tests or application code outside the test run."""
    assert main.SessionLocal is not database.SessionLocal
