from pydantic import BaseModel, EmailStr, field_validator

# Module 19 — self/admin editable recipient fields. EMAIL/WHATSAPP/BOTH/NONE.
NOTIFICATION_PREFERENCES = ["EMAIL", "WHATSAPP", "BOTH", "NONE"]


class UserCreate(BaseModel):
    email: EmailStr
    name: str
    password: str
    phone: str | None = None
    role_codes: list[str] = []
    hospital_scope_id: str | None = None


MIN_PASSWORD_LENGTH = 8


class UserUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    is_active: bool | None = None
    role_codes: list[str] | None = None
    hospital_scope_id: str | None = None
    phone: str | None = None
    whatsapp_number: str | None = None
    department_id: str | None = None
    notification_preference: str | None = None

    @field_validator("notification_preference")
    @classmethod
    def validate_preference(cls, value: str | None) -> str | None:
        if value is not None and value not in NOTIFICATION_PREFERENCES:
            raise ValueError(f"notification_preference must be one of {NOTIFICATION_PREFERENCES}")
        return value


class UserPasswordUpdate(BaseModel):
    """Super-Admin password reset (PATCH /api/v1/users/{id} is deliberately kept clean
    of password handling — a dedicated endpoint/schema means the hashed value is
    never on a path that could accidentally echo the plaintext back or into an audit
    payload alongside unrelated field changes)."""

    new_password: str
    confirm_password: str

    @field_validator("new_password")
    @classmethod
    def validate_policy(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
        return value

    @field_validator("confirm_password")
    @classmethod
    def validate_match(cls, value: str, info) -> str:
        if info.data.get("new_password") is not None and value != info.data["new_password"]:
            raise ValueError("Passwords do not match")
        return value


class UserContactUpdate(BaseModel):
    """Module 19 — self-service contact-field update (PATCH /api/v1/auth/me). Deliberately
    excludes name/is_active/role_codes/hospital_scope_id, which stay admin-only
    (USERS_MANAGE) via UserUpdate/PATCH /api/v1/users/{id})."""

    phone: str | None = None
    whatsapp_number: str | None = None
    department_id: str | None = None
    notification_preference: str | None = None

    @field_validator("notification_preference")
    @classmethod
    def validate_preference(cls, value: str | None) -> str | None:
        if value is not None and value not in NOTIFICATION_PREFERENCES:
            raise ValueError(f"notification_preference must be one of {NOTIFICATION_PREFERENCES}")
        return value


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    is_active: bool
    hospital_scope_id: str | None = None
    phone: str | None = None
    whatsapp_number: str | None = None
    department_id: str | None = None
    notification_preference: str = "EMAIL"
    roles: list[str] = []

    model_config = {"from_attributes": True}


class RoleOut(BaseModel):
    id: str
    code: str
    name: str
    description: str | None = None
    permissions: list[str] = []

    model_config = {"from_attributes": True}


class RoleCreate(BaseModel):
    code: str
    name: str
    description: str | None = None
    permission_codes: list[str] = []


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    permission_codes: list[str] | None = None


class PermissionOut(BaseModel):
    id: str
    code: str
    description: str | None = None

    model_config = {"from_attributes": True}
