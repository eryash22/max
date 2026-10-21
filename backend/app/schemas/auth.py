from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CurrentUser(BaseModel):
    id: str
    email: str
    name: str
    is_active: bool
    roles: list[str]
    permissions: list[str]
    hospital_scope_id: str | None = None
    whatsapp_number: str | None = None
    department_id: str | None = None
    notification_preference: str = "EMAIL"
