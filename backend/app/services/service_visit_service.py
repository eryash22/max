from sqlalchemy.orm import Session

from ..audit.service import record as audit_record
from ..models.incident import ServiceVisit, ServiceVisitType
from ..models.procurement import PurchaseOrder
from ..schemas.notification import EVENT_VENDOR_VISIT_SCHEDULED
from ..schemas.service_visit import VISIT_TYPES, ServiceVisitIn, ServiceVisitUpdateIn
from .communication_service import dispatch_procurement_event_safe


def ensure_default_service_visit_types(db: Session) -> None:
    """Idempotently seed the five default `ServiceVisitType` rows (Batch E, Part 20).

    Migration 0018 already does this against the real (Postgres) dev/prod database;
    this mirrors it at the application layer so a freshly created schema — the
    SQLite test suite's `Base.metadata.create_all()`, which runs no Alembic
    migrations — has the same defaults available, exactly like
    `ensure_roles_and_permissions` mirrors migration-time role/permission seeding.
    Never removes or renames a row an admin already changed; only adds a missing
    default type."""
    existing_names = {name for (name,) in db.query(ServiceVisitType.name).all()}
    for name in VISIT_TYPES:
        if name not in existing_names:
            db.add(ServiceVisitType(name=name, is_active=True))
    db.commit()


def create_visit(db: Session, payload: ServiceVisitIn, *, user_id: str) -> ServiceVisit:
    data = payload.model_dump()
    visit = ServiceVisit(**data)
    db.add(visit)
    db.flush()
    audit_record(db, user_id=user_id, action="CREATE", entity="service_visit", entity_id=visit.id, new_value=data)
    db.commit()
    db.refresh(visit)
    # Module 21 — Procurement. A visit scheduled against a PO is the "vendor visit
    # scheduled" notification trigger (-> BME); every other ServiceVisit use
    # (PM/calibration/breakdown with no purchase_order_id) is unaffected.
    if visit.purchase_order_id:
        po = db.get(PurchaseOrder, visit.purchase_order_id)
        if po is not None:
            dispatch_procurement_event_safe(
                db, EVENT_VENDOR_VISIT_SCHEDULED, entity_type="purchase_order", entity=po, user_id=user_id,
            )
    return visit


def update_visit(db: Session, visit: ServiceVisit, payload: ServiceVisitUpdateIn, *, user_id: str) -> ServiceVisit:
    before = {c.name: getattr(visit, c.name) for c in visit.__table__.columns}
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(visit, field, value)
    audit_record(db, user_id=user_id, action="UPDATE", entity="service_visit", entity_id=visit.id,
                 old_value=before, new_value=changes)
    db.commit()
    db.refresh(visit)
    return visit


def delete_visit(db: Session, visit: ServiceVisit, *, user_id: str) -> None:
    before = {c.name: getattr(visit, c.name) for c in visit.__table__.columns}
    audit_record(db, user_id=user_id, action="DELETE", entity="service_visit", entity_id=visit.id, old_value=before)
    db.delete(visit)
    db.commit()
