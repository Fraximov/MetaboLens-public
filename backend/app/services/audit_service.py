from sqlalchemy.orm import Session

from app.models.models import AuditEvent


class AuditService:
    def __init__(self, db: Session):
        self.db = db

    def log(self, actor_user_id: int | None, event_type: str, target_type: str, target_id: str, payload: dict | None = None) -> AuditEvent:
        event = AuditEvent(
            actor_user_id=actor_user_id,
            event_type=event_type,
            target_type=target_type,
            target_id=target_id,
            payload_json=payload or {},
        )
        self.db.add(event)
        return event
