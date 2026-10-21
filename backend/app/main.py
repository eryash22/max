from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models  # noqa: F401 ensures all tables register on Base.metadata
from .config import settings
from .database import SessionLocal
from .routers import (
    audit,
    auth,
    business_impact,
    calibration,
    contracts,
    dashboard,
    devices,
    documents,
    escalation,
    fulfillments,
    hospitals,
    imports,
    incidents,
    inventory,
    notifications,
    pm,
    procurement,
    qr,
    qr_public,
    requirements,
    review_queue,
    roles,
    service_visits,
    system,
    theme,
    users,
    vendors,
)
from .services.seed_service import ensure_roles_and_permissions
from .services.service_visit_service import ensure_default_service_visit_types

app = FastAPI(title="MAX Biomedical AI", version="0.2.0", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- /api Prefix Router ---
api_router = APIRouter()

api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(roles.router)
api_router.include_router(hospitals.router)
api_router.include_router(devices.router)
api_router.include_router(inventory.router)
api_router.include_router(imports.router)
api_router.include_router(incidents.router)
api_router.include_router(pm.router)
api_router.include_router(calibration.router)
api_router.include_router(vendors.router)
api_router.include_router(contracts.router)
api_router.include_router(documents.router)
api_router.include_router(escalation.router)
api_router.include_router(notifications.router)
api_router.include_router(business_impact.assumptions_router)
api_router.include_router(business_impact.analytics_router)
api_router.include_router(service_visits.router)
api_router.include_router(requirements.router)
api_router.include_router(fulfillments.router)
api_router.include_router(review_queue.router)
api_router.include_router(audit.router)
api_router.include_router(procurement.router)
api_router.include_router(theme.router)
api_router.include_router(dashboard.router)
api_router.include_router(qr.router)
api_router.include_router(qr_public.router)

# Mount /api routes into app
app.include_router(api_router)


@app.on_event("startup")
def startup():
    settings.file_storage_path.mkdir(parents=True, exist_ok=True)
    (settings.file_storage_path / "imports").mkdir(parents=True, exist_ok=True)
    (settings.file_storage_path / "documents").mkdir(parents=True, exist_ok=True)
    # Seed the permission catalog and default role bundles. Safe no-op once already seeded;
    # if the schema hasn't been migrated yet this fails silently so `uvicorn` can still boot
    # for local iteration without a live database (system health surfaces the real state).
    db = SessionLocal()
    try:
        ensure_roles_and_permissions(db)
        ensure_default_service_visit_types(db)
    except Exception:
        db.rollback()
    finally:
        db.close()
