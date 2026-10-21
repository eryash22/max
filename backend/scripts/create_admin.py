"""Interactive bootstrap for the first Super Admin user.

Usage (from backend/, with the virtualenv active and .env configured):
    python scripts/create_admin.py

This never hard-codes a password. It prompts for one, hashes it with the
same bcrypt scheme the login endpoint verifies against, and idempotently
ensures the SUPER_ADMIN role/permission bundle exists before assigning it.
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models.identity import Role, User, UserRole  # noqa: E402
from app.security.password import hash_password  # noqa: E402
from app.security.permissions import SUPER_ADMIN  # noqa: E402
from app.services.seed_service import ensure_roles_and_permissions  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        ensure_roles_and_permissions(db)

        email = input("Admin email: ").strip().lower()
        if not email or "@" not in email:
            print("A valid email is required.")
            sys.exit(1)

        if db.query(User).filter(User.email == email).first():
            print(f"A user with email {email} already exists.")
            sys.exit(1)

        name = input("Full name: ").strip()
        if not name:
            print("Name is required.")
            sys.exit(1)

        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if len(password) < 12:
            print("Password must be at least 12 characters.")
            sys.exit(1)
        if password != confirm:
            print("Passwords did not match.")
            sys.exit(1)

        role = db.query(Role).filter(Role.code == SUPER_ADMIN).first()
        if role is None:
            print("SUPER_ADMIN role missing after seeding; check database connectivity.")
            sys.exit(1)

        user = User(email=email, name=name, hashed_password=hash_password(password), is_active=True)
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()

        print(f"Created Super Admin user {email}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
