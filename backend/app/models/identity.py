from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import BaseModel


class Role(BaseModel):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)

    permissions: Mapped[list["RolePermission"]] = relationship(back_populates="role", cascade="all, delete-orphan")
    users: Mapped[list["UserRole"]] = relationship(back_populates="role", cascade="all, delete-orphan")


class Permission(BaseModel):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)

    roles: Mapped[list["RolePermission"]] = relationship(back_populates="permission", cascade="all, delete-orphan")


class RolePermission(BaseModel):
    __tablename__ = "role_permissions"

    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"))
    permission_id: Mapped[str] = mapped_column(ForeignKey("permissions.id"))

    role: Mapped["Role"] = relationship(back_populates="permissions")
    permission: Mapped["Permission"] = relationship(back_populates="roles")


class User(BaseModel):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    hashed_password: Mapped[str] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bumped on every admin-driven password reset so JWTs issued before the reset stop
    # being accepted immediately (dependencies/auth.py compares this to the token's
    # "tv" claim) instead of staying valid for up to ACCESS_TOKEN_EXPIRE_MINUTES more.
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hospital_scope_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    # Module 19 — communication recipient fields. notification_preference values:
    # EMAIL, WHATSAPP, BOTH, NONE. Stored/editable here; NOT yet consumed by
    # communication_service.dispatch_event's recipient resolution — that would touch
    # the core dispatch loop and is an explicitly descoped follow-on, see
    # PROJECT_STATUS.md Module 19 known issues.
    # Generic contact number, separate from whatsapp_number: that one is the WhatsApp
    # delivery address consumed by communication_service, so it must not be overloaded
    # as a general phone number (see migration 0024).
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    whatsapp_number: Mapped[str | None] = mapped_column(String, nullable=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    notification_preference: Mapped[str] = mapped_column(String, default="EMAIL")

    roles: Mapped[list["UserRole"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserRole(BaseModel):
    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"))

    user: Mapped["User"] = relationship(back_populates="roles")
    role: Mapped["Role"] = relationship(back_populates="users")
