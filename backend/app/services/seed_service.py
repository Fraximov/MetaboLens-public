import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.security import hash_password
from app.models.models import Project, User, WorkflowDefinition
from app.services.role_service import RoleService

# Pre-engine MS-DIAL definitions, retired 2026-10-03; their jobs stay readable.
LEGACY_WORKFLOW_NAMES = ("msdial5-nextflow", "metaboigniter-mock")


class SeedService:
    def __init__(self, db: Session):
        self.db = db
        self.roles = RoleService(db)

    def run(self) -> None:
        settings = get_settings()
        if settings.app_env == "dev" and settings.auto_seed:
            self._ensure_user("admin@metabolens.local", "Admin", "admin123")
            self._ensure_user("operator@metabolens.local", "Operator", "operator123")
            self._ensure_user("submitter@metabolens.local", "Submitter", "submitter123")
        if settings.bootstrap_admin_email:
            existing = self.db.query(User).filter(User.email == settings.bootstrap_admin_email).first()
            if existing is None:
                user = self._ensure_user(str(settings.bootstrap_admin_email), "Administrator", settings.bootstrap_admin_password.get_secret_value())
                self.roles.assign_role(user.id, "admin", granted_by=None, study_id=None)
        self._ensure_project()
        self._retire_legacy_workflow_definitions()
        self._ensure_metabolensr_engine_workflow_definition()
        self._ensure_metabolensr_engine_workflow_definition('masscube')
        self.db.commit()

    def _ensure_user(self, email: str, display_name: str, password: str) -> User:
        user = self.db.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                email=email,
                display_name=display_name,
                password_hash=hash_password(password),
                auth_provider="local",
                is_active=True,
            )
            self.db.add(user)
            self.db.flush()

        self._ensure_seed_roles(user)
        return user

    def _ensure_seed_roles(self, user: User) -> None:
        if user.email == "admin@metabolens.local":
            self.roles.assign_role(user.id, "admin", granted_by=None, study_id=None)
        if user.email == "operator@metabolens.local":
            self.roles.assign_role(user.id, "operator", granted_by=None, study_id=None)

    def _ensure_project(self) -> Project:
        project = self.db.query(Project).filter(Project.code == "DEMO").first()
        if project:
            return project
        project = Project(code="DEMO", title="Demo Metabolomics Project", description="Seed project")
        self.db.add(project)
        return project

    def _retire_legacy_workflow_definitions(self) -> None:
        """Keep pre-engine MS-DIAL definitions for their historical jobs, never for new launches."""

        legacy = self.db.query(WorkflowDefinition).filter(WorkflowDefinition.name.in_(LEGACY_WORKFLOW_NAMES))
        for workflow in legacy:
            workflow.is_active = False

    def _ensure_metabolensr_engine_workflow_definition(self, engine_id: str = 'msdial5') -> WorkflowDefinition | None:
        """Register the package-owned engine workflow when its checked-in files are mounted.

        Engine workflows are the only launchable workflows. A deployment without
        the workflow files has none.
        """
        pipeline_root = None
        for parent in Path(__file__).resolve().parents:
            candidate = parent / "workflows" / "nextflow4ms-dial-msdial5-clean"
            if (candidate / "engines" / engine_id / "engine.json").is_file() and (candidate / "engines" / engine_id / "parameters.json").is_file():
                pipeline_root = candidate
                break
        if pipeline_root is None:
            return None
        descriptor_path = pipeline_root / "engines" / engine_id / "engine.json"
        schema_path = pipeline_root / "engines" / engine_id / "parameters.json"
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        parameter_schema = json.loads(schema_path.read_text(encoding="utf-8"))
        workflow = self.db.query(WorkflowDefinition).filter(
            WorkflowDefinition.name == f"metabolensr-{engine_id}",
        ).first()
        if workflow is None:
            workflow = WorkflowDefinition(name=f"metabolensr-{engine_id}")
            self.db.add(workflow)
        workflow.version = str(descriptor["package"]["version"])
        workflow.container_profile = "docker"
        workflow.nextflow_entrypoint = str(descriptor["entrypoint"])
        workflow.parameter_schema_json = parameter_schema
        workflow.execution_backend = "nextflow"
        workflow.execution_profile = str(descriptor["execution_profiles"][0])
        workflow.pipeline_root = None
        workflow.default_nextflow_profile = "docker"
        default_inputs = {"msdial5": {
            "msdial_config_path": "functional_test/sample_data/msdial_params.txt",
            "ms1_library_path": "libraries/ms1.txt",
            "ms2_library_path": "libraries/ms2.msp",
            "is_library_path": "libraries/internal-standards.txt",
        }, "masscube": {
            "masscube_config_path": "templates/masscube/orbitrap-msdial-comparison-v1.csv",
        }}
        workflow.static_input_config_json = {"engine": descriptor, **default_inputs.get(engine_id, {})}
        workflow.is_active = True
        return workflow
