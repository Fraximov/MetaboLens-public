from app.core.errors import forbidden


ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLE_SUBMITTER = "submitter"
ROLE_VIEWER = "viewer"


class PermissionChecker:
    def __init__(self, role_service):
        self.role_service = role_service

    def can_read_study(self, user_id: int, study_id: int) -> bool:
        return self.role_service.has_any_role(user_id, study_id, {ROLE_ADMIN, ROLE_OPERATOR, ROLE_SUBMITTER, ROLE_VIEWER})

    def can_edit_study(self, user_id: int, study_id: int, study_status: str) -> bool:
        if study_status != "draft":
            return False
        return self.role_service.has_any_role(user_id, study_id, {ROLE_ADMIN, ROLE_SUBMITTER})

    def require_edit_study(self, user_id: int, study_id: int, study_status: str) -> None:
        if study_status != "draft":
            raise forbidden(f"Study status is '{study_status}'. Samples, metadata, and files are editable only in 'draft'.")
        if not self.role_service.has_any_role(user_id, study_id, {ROLE_ADMIN, ROLE_SUBMITTER}):
            raise forbidden("Submitter or admin role is required for this study.")

    def can_review_study(self, user_id: int, study_id: int) -> bool:
        return self.role_service.has_any_role(user_id, study_id, {ROLE_ADMIN, ROLE_OPERATOR})

    def can_manage_submission(self, user_id: int, study_id: int, study_status: str) -> bool:
        """Submission metadata and packages: operators and admins always, submitters while in draft."""
        return self.can_review_study(user_id, study_id) or self.can_edit_study(user_id, study_id, study_status)

    def can_edit_sample_details(self, user_id: int, study_id: int, study_status: str) -> bool:
        """Correcting a sample's name or characteristics must not require reprocessing.

        Workflow jobs freeze file and sample identities, not these values, so edits
        stay possible after a run. Adding or removing samples and files stays draft-only.
        """
        if study_status == "archived":
            return False
        return self.role_service.has_any_role(user_id, study_id, {ROLE_ADMIN, ROLE_OPERATOR, ROLE_SUBMITTER})

    def can_operate_workflows(self, user_id: int, study_id: int) -> bool:
        """Launch, continue, retry or delete workflow jobs and edit feature reviews."""
        return self.role_service.has_any_role(user_id, study_id, {ROLE_ADMIN, ROLE_OPERATOR})

    def can_manage_roles(self, user_id: int) -> bool:
        return self.role_service.has_global_role(user_id, ROLE_ADMIN)

def require_allowed(allowed: bool, message: str = "Forbidden") -> None:
    if not allowed:
        raise forbidden(message)
