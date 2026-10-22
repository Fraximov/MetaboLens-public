from sqlalchemy.orm import Session

from app.core.errors import unauthorized
from app.core.security import create_access_token, verify_password
from app.models.models import User
from app.services.audit_service import AuditService


class AuthService:
    def __init__(self, db: Session):
        self.db = db
        self.audit = AuditService(db)

    def login(self, email: str, password: str) -> str:
        user = self.db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
        if not user or not user.password_hash or not verify_password(password, user.password_hash):
            raise unauthorized("Invalid credentials")

        token = create_access_token(subject=user.email)
        self.audit.log(actor_user_id=user.id, event_type="auth.login", target_type="user", target_id=str(user.id))
        self.db.commit()
        return token
