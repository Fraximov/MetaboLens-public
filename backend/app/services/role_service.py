from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.permissions import PermissionChecker, require_allowed
from app.models.models import RoleAssignment
from app.services.audit_service import AuditService


class RoleService:
    def __init__(self, db: Session):
        self.db = db

    def has_global_role(self, user_id: int, role: str) -> bool:
        return (
            self.db.query(RoleAssignment)
            .filter(
                RoleAssignment.user_id == user_id,
                RoleAssignment.study_id.is_(None),
                RoleAssignment.role == role,
            )
            .first()
            is not None
        )

    def has_any_role(self, user_id: int, study_id: int, roles: set[str]) -> bool:
        return (
            self.db.query(RoleAssignment)
            .filter(
                RoleAssignment.user_id == user_id,
                RoleAssignment.role.in_(list(roles)),
                or_(RoleAssignment.study_id.is_(None), RoleAssignment.study_id == study_id),
            )
            .first()
            is not None
        )

    def assign_role(self, user_id: int, role: str, granted_by: int | None, study_id: int | None = None) -> RoleAssignment:
        existing = (
            self.db.query(RoleAssignment)
            .filter(
                and_(
                    RoleAssignment.user_id == user_id,
                    RoleAssignment.role == role,
                    RoleAssignment.study_id == study_id,
                )
            )
            .first()
        )
        if existing:
            return existing

        assignment = RoleAssignment(user_id=user_id, role=role, study_id=study_id, granted_by=granted_by)
        self.db.add(assignment)
        return assignment

    def grant(self, admin_user_id: int, user_id: int, role: str, study_id: int | None) -> None:
        """Assign a role on an admin's request and record it in the audit log."""
        require_allowed(PermissionChecker(self).can_manage_roles(admin_user_id), "Only admin can assign roles")
        self.assign_role(user_id=user_id, role=role, study_id=study_id, granted_by=admin_user_id)
        AuditService(self.db).log(
            actor_user_id=admin_user_id,
            event_type="role.assign",
            target_type="role_assignment",
            target_id="new",
            payload={"user_id": user_id, "role": role, "study_id": study_id},
        )
        self.db.commit()
