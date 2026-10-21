from sqlalchemy.orm import Session

from ..models.identity import User
from ..security.password import verify_password
from ..security.tokens import create_access_token


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email.lower()).first()
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def issue_token(user: User) -> str:
    return create_access_token(subject=user.id, token_version=user.token_version)
