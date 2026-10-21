from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel


class HospitalNetwork(BaseModel):
    __tablename__ = "hospital_networks"

    code: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String, unique=True)

    branches: Mapped[list["Branch"]] = relationship(back_populates="network")


class Branch(BaseModel):
    __tablename__ = "branches"

    network_id: Mapped[str | None] = mapped_column(ForeignKey("hospital_networks.id"), nullable=True)
    code: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    source_name: Mapped[str | None] = mapped_column(String, nullable=True)
    region: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    # Legacy free-text fields (pre-final-hardening-pass). Preserved as-is for existing
    # data/back-compat display fallback — no longer the source of truth for who the
    # branch's Unit Head/TLs are; see unit_head_user_id / BranchTL below.
    unit_head: Mapped[str | None] = mapped_column(String, nullable=True)
    tl: Mapped[str | None] = mapped_column(String, nullable=True)
    # Real branch user hierarchy (final hardening pass): the branch's Unit Head as an
    # actual User row. Nullable — not every branch has one assigned yet, and legacy
    # branches fall back to the `unit_head` free-text field for display. One Unit Head
    # per branch; TLs are many-to-one via the BranchTL table below.
    unit_head_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    branch_email: Mapped[str | None] = mapped_column(String, nullable=True)
    branch_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    branch_whatsapp: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")
    inventory_mapping_status: Mapped[str] = mapped_column(String, default="Inventory Pending")
    environment: Mapped[str] = mapped_column(String, default="DEVELOPMENT / DEMO DATA")
    # Business-workflow audit: the central biomedical warehouse ("MAX @ HOME, Okhla
    # Phase 2") is a Branch row with this flag set, not a separate entity —
    # InventoryLocation.branch_id is a required FK to Branch, so this is the minimal
    # way to distinguish "the one central store" from an ordinary hospital branch
    # without inventing parallel warehouse architecture.
    is_central_warehouse: Mapped[bool] = mapped_column(Boolean, default=False)

    network: Mapped["HospitalNetwork | None"] = relationship(back_populates="branches")
    buildings: Mapped[list["Building"]] = relationship(back_populates="branch")
    departments: Mapped[list["Department"]] = relationship(back_populates="branch")
    tl_assignments: Mapped[list["BranchTL"]] = relationship(back_populates="branch", cascade="all, delete-orphan")


class BranchTL(BaseModel):
    """Real branch user hierarchy (final hardening pass): a branch may have one or
    more TL users, each a real User row. Separate join table (rather than a single FK
    like Branch.unit_head_user_id) because this is many-per-branch."""

    __tablename__ = "branch_tls"

    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))

    branch: Mapped["Branch"] = relationship(back_populates="tl_assignments")


class Building(BaseModel):
    __tablename__ = "buildings"

    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String)

    branch: Mapped["Branch"] = relationship(back_populates="buildings")
    floors: Mapped[list["Floor"]] = relationship(back_populates="building")


class Floor(BaseModel):
    __tablename__ = "floors"

    building_id: Mapped[str] = mapped_column(ForeignKey("buildings.id"))
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String)

    building: Mapped["Building"] = relationship(back_populates="floors")
    departments: Mapped[list["Department"]] = relationship(back_populates="floor")


class DepartmentMaster(BaseModel):
    """Central department catalog (Physiotherapy, ICU, ...) — the shared definition
    a Department (branch assignment) row points to. Same shape as the existing
    DeviceCategory/DeviceModel split: one global master, many branch-scoped
    instances referencing it. name/code are unique here (not on Department, which
    may legitimately have many rows sharing one master's name across branches)."""

    __tablename__ = "department_masters"

    code: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")

    assignments: Mapped[list["Department"]] = relationship(back_populates="master")


class Department(BaseModel):
    """A department MASTER assigned/available at one branch — every existing
    Requirement/Incident/Asset/User/Location/AssetHistory department_id reference
    continues to point here, not at DepartmentMaster, because those workflows are
    inherently about a department at a specific branch, not the abstract concept.

    department_master_id is nullable at the schema level only for migration safety;
    the API requires it on every new assignment. name/code are kept for backward
    compatibility with existing readers, but once linked they are master-controlled
    -- synced from DepartmentMaster on every create/update, never edited here
    independently. See routers/hospitals.py's department endpoints."""

    __tablename__ = "departments"

    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    floor_id: Mapped[str | None] = mapped_column(ForeignKey("floors.id"), nullable=True)
    department_master_id: Mapped[str | None] = mapped_column(ForeignKey("department_masters.id"), nullable=True)
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")

    branch: Mapped["Branch"] = relationship(back_populates="departments")
    floor: Mapped["Floor | None"] = relationship(back_populates="departments")
    master: Mapped["DepartmentMaster | None"] = relationship(back_populates="assignments")
    locations: Mapped[list["Location"]] = relationship(back_populates="department")


class Location(BaseModel):
    __tablename__ = "locations"

    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"))
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String)
    room: Mapped[str | None] = mapped_column(String, nullable=True)
    bed: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")

    department: Mapped["Department"] = relationship(back_populates="locations")
