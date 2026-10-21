"""The application's startup seeding must exist and must work on an empty database.

This is a regression guard. The `@app.on_event("startup")` handler was silently lost
during an unrelated refactor of `app/main.py`; the imports it used stayed behind, so
nothing looked broken — but a freshly created database would have come up with no
roles, no permissions and no default service visit types, leaving nobody able to log in
with any authority.

Nothing in the suite covered it, because every test seeds through the `admin_client`
fixture rather than through application startup. These tests exercise
`app.main.startup()` itself against an empty schema.
"""
import app.main as main
from app.database import Base
from app.models.identity import Permission, Role, RolePermission
from app.security.permissions import ALL_PERMISSIONS, DEFAULT_ROLE_PERMISSIONS


def _empty_database(monkeypatch):
    """A brand-new schema with no rows, wired in as the app's session factory."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    # startup() resolves SessionLocal from its own module globals.
    monkeypatch.setattr(main, "SessionLocal", factory)
    return engine, factory


def test_startup_handler_is_registered():
    """The handler existing at all is the thing that regressed — assert it is wired to
    the app, not merely that a function with that name is importable."""
    assert callable(getattr(main, "startup", None)), "app.main.startup() must exist"

    handlers = main.app.router.on_startup
    names = {getattr(h, "__name__", "") for h in handlers}
    assert "startup" in names, "startup() must be registered as an app startup event"


def test_startup_seeds_roles_and_permissions_into_an_empty_database(monkeypatch):
    engine, factory = _empty_database(monkeypatch)
    session = factory()
    try:
        assert session.query(Role).count() == 0, "precondition: database starts empty"

        main.startup()

        assert session.query(Permission).count() == len(ALL_PERMISSIONS), (
            "every permission in the catalog must be seeded"
        )
        seeded_roles = {r.code for r in session.query(Role).all()}
        assert seeded_roles == set(DEFAULT_ROLE_PERMISSIONS), (
            "every default role must be seeded"
        )
        assert session.query(RolePermission).count() > 0, (
            "roles must be bound to their permissions, not just created"
        )
    finally:
        session.close()
        engine.dispose()


def test_startup_seeds_default_service_visit_types(monkeypatch):
    from app.models.incident import ServiceVisitType

    engine, factory = _empty_database(monkeypatch)
    session = factory()
    try:
        assert session.query(ServiceVisitType).count() == 0
        main.startup()
        assert session.query(ServiceVisitType).count() > 0, (
            "default service visit types must be seeded at startup"
        )
    finally:
        session.close()
        engine.dispose()


def test_super_admin_receives_every_permission(monkeypatch):
    """The specific consequence of the regression: without seeding there is no role
    holding the administrative permissions, so nobody can administer the system."""
    engine, factory = _empty_database(monkeypatch)
    session = factory()
    try:
        main.startup()

        super_admin = session.query(Role).filter(Role.code == "SUPER_ADMIN").first()
        assert super_admin is not None
        granted = {rp.permission.code for rp in super_admin.permissions}
        assert granted == set(ALL_PERMISSIONS), (
            "Super Admin must hold the full permission catalog after seeding"
        )
    finally:
        session.close()
        engine.dispose()


def test_startup_is_idempotent(monkeypatch):
    """Startup runs on every boot, so a second run must change nothing — no duplicate
    roles, permissions, bindings or visit types."""
    from app.models.incident import ServiceVisitType

    engine, factory = _empty_database(monkeypatch)
    session = factory()
    try:
        main.startup()
        first = (
            session.query(Role).count(),
            session.query(Permission).count(),
            session.query(RolePermission).count(),
            session.query(ServiceVisitType).count(),
        )

        main.startup()
        main.startup()
        after = (
            session.query(Role).count(),
            session.query(Permission).count(),
            session.query(RolePermission).count(),
            session.query(ServiceVisitType).count(),
        )

        assert first == after, f"startup must be idempotent, got {first} then {after}"
    finally:
        session.close()
        engine.dispose()


def test_startup_creates_the_storage_directories(monkeypatch, tmp_path):
    """The same handler also prepares the upload directories — document/import upload
    fails without them."""
    from app.config import settings

    monkeypatch.setattr(settings, "file_storage_path", tmp_path / "storage")
    _engine, _factory = _empty_database(monkeypatch)

    main.startup()

    assert (tmp_path / "storage").is_dir()
    assert (tmp_path / "storage" / "imports").is_dir()
    assert (tmp_path / "storage" / "documents").is_dir()
    _engine.dispose()


def test_startup_survives_an_unmigrated_database(monkeypatch):
    """Deliberate pre-existing behaviour: uvicorn must still boot against a database
    whose schema isn't migrated yet, so local iteration isn't blocked. The health
    endpoint is what surfaces the real state."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # No create_all() — the tables do not exist.
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=engine, autoflush=False, autocommit=False))

    main.startup()  # must not raise

    engine.dispose()
