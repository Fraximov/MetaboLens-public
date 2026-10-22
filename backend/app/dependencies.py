import secrets

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import unauthorized
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    settings = get_settings()
    if settings.app_env == "dev" and settings.testing_god_token and secrets.compare_digest(token, settings.testing_god_token):
        user = (
            db.query(User)
            .filter(User.email == settings.testing_god_user_email, User.is_active.is_(True))
            .first()
        )
        if not user:
            raise unauthorized("Testing god token user not found or inactive")
        return user

    subject = decode_access_token(token)
    if not subject:
        raise unauthorized("Invalid token")

    user = db.query(User).filter(User.email == subject, User.is_active.is_(True)).first()
    if not user:
        raise unauthorized("User not found or inactive")
    return user
