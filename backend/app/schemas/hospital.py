from pydantic import BaseModel


class HospitalNetworkIn(BaseModel):
    code: str | None = None
    name: str


class HospitalNetworkOut(HospitalNetworkIn):
    id: str

    model_config = {"from_attributes": True}


class BranchIn(BaseModel):
    network_id: str | None = None
    code: str | None = None
    name: str
    source_name: str | None = None
    region: str | None = None
    state: str | None = None
    city: str | None = None
    address: str | None = None
    # Legacy free-text fields — preserved for back-compat, no longer the source of
    # truth for who the branch's Unit Head/TLs are. See unit_head_user_id below and
    # BranchTLOut / the /branches/{id}/tls endpoints for the real assignments.
    unit_head: str | None = None
    tl: str | None = None
    # Real branch user hierarchy: the branch's Unit Head as an actual User row.
    unit_head_user_id: str | None = None
    branch_email: str | None = None
    branch_phone: str | None = None
    branch_whatsapp: str | None = None
    status: str = "ACTIVE"
    # Central biomedical warehouse ("MAX @ HOME, Okhla Phase 2") flag — see
    # Branch.is_central_warehouse. False for every ordinary hospital branch.
    is_central_warehouse: bool = False


class BranchOut(BranchIn):
    id: str
    inventory_mapping_status: str
    environment: str

    model_config = {"from_attributes": True}


class BranchTLIn(BaseModel):
    user_id: str


class BranchTLOut(BaseModel):
    id: str
    branch_id: str
    user_id: str

    model_config = {"from_attributes": True}


class BuildingIn(BaseModel):
    branch_id: str
    code: str | None = None
    name: str


class BuildingOut(BuildingIn):
    id: str

    model_config = {"from_attributes": True}


class FloorIn(BaseModel):
    building_id: str
    code: str | None = None
    name: str


class FloorOut(FloorIn):
    id: str

    model_config = {"from_attributes": True}


class DepartmentMasterIn(BaseModel):
    name: str
    code: str | None = None
    status: str = "ACTIVE"


class DepartmentMasterOut(DepartmentMasterIn):
    id: str

    model_config = {"from_attributes": True}


class DepartmentIn(BaseModel):
    """A branch assignment of an existing DepartmentMaster. name/code are never
    accepted here -- they are always derived from the master (see
    routers/hospitals.py's create_department/update_department)."""

    branch_id: str
    department_master_id: str
    floor_id: str | None = None
    status: str = "ACTIVE"


class DepartmentOut(BaseModel):
    id: str
    branch_id: str
    floor_id: str | None = None
    department_master_id: str | None = None
    code: str | None = None
    name: str
    status: str

    model_config = {"from_attributes": True}


class LocationIn(BaseModel):
    department_id: str
    code: str | None = None
    name: str
    room: str | None = None
    bed: str | None = None
    status: str = "ACTIVE"


class LocationOut(LocationIn):
    id: str

    model_config = {"from_attributes": True}
