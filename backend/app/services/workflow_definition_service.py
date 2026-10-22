"""Workflow definition and library catalog helpers.

This module owns read-only workflow definition lookup plus validation of
workflow-bundled library paths. It does not create jobs or mutate workflow
state.
"""

import hashlib
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import bad_request, not_found
from app.models.models import WorkflowDefinition
from app.services.masscube_config import MAX_TEMPLATE_BYTES, parse_masscube_template, validate_masscube_parameters


class WorkflowDefinitionService:
    MAX_MSDIAL_TEMPLATE_BYTES = 64 * 1024

    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    def list_definitions(self) -> list[WorkflowDefinition]:
        return self.db.query(WorkflowDefinition).filter(WorkflowDefinition.is_active.is_(True)).order_by(WorkflowDefinition.id).all()

    def list_libraries(self, user_id: int, workflow_definition_id: int) -> dict:
        if not user_id:
            raise bad_request("Authenticated user is required")
        workflow = self.active_definition(workflow_definition_id)

        pipeline_root = self.resolve_pipeline_root(workflow)
        library_root = self.resolve_library_root(workflow)
        static_inputs = dict(workflow.static_input_config_json or {})
        default_msdial_config_path = self.default_template_value(static_inputs.get("msdial_config_path"))
        default_masscube = static_inputs.get('masscube_config_path')
        masscube_templates = []
        for candidate in sorted((pipeline_root / 'templates/masscube').glob('*.csv')):
            try:
                relative = self.validate_masscube_config_path(workflow, str(candidate))
            except HTTPException:
                continue
            masscube_templates.append({'label': candidate.stem.replace('-', ' '), 'value': relative})
        return {
            "ms1_libraries": self.scan_library_options(library_root, ".txt"),
            "ms2_libraries": self.scan_library_options(library_root, ".msp"),
            "is_libraries": self.scan_library_options(library_root, ".txt"),
            "msdial_templates": self.scan_msdial_template_options(pipeline_root, default_msdial_config_path),
            "default_ms1_library_path": self.default_library_value(static_inputs.get("ms1_library_path")),
            "default_ms2_library_path": self.default_library_value(static_inputs.get("ms2_library_path")),
            "default_is_library_path": self.default_library_value(static_inputs.get("is_library_path")),
            "default_msdial_config_path": default_msdial_config_path,
            'masscube_templates': masscube_templates,
            'default_masscube_config_path': default_masscube,
        }

    def validate_masscube_config_path(self, workflow: WorkflowDefinition, value: str) -> str:
        root = self.resolve_pipeline_root(workflow)
        candidate = (root / value).resolve()
        if (not candidate.is_relative_to(root / 'templates/masscube')
                or candidate.suffix.lower() != '.csv' or not candidate.is_file()):
            raise bad_request('MassCube template must be an existing CSV inside templates/masscube')
        return candidate.relative_to(root).as_posix()

    def get_masscube_template_content(self, user_id: int, workflow_definition_id: int, value: str) -> dict:
        if not user_id:
            raise bad_request('Authenticated user is required')
        workflow = self.active_definition(workflow_definition_id)
        if 'masscube_config_path' not in (workflow.parameter_schema_json or {}).get('properties', {}):
            raise bad_request('This workflow does not support MassCube templates')
        relative = self.validate_masscube_config_path(workflow, value)
        try:
            with (self.resolve_pipeline_root(workflow) / relative).open('rb') as handle:
                content = handle.read(MAX_TEMPLATE_BYTES + 1).decode('utf-8')
            parse_masscube_template(content, workflow.parameter_schema_json)
        except (OSError, ValueError) as exc:
            raise bad_request(f'Unable to load MassCube template: {exc}') from exc
        return {'path': relative, 'content': content,
                'checksum_sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()}

    def resolve_masscube_template(self, user_id: int, workflow: WorkflowDefinition,
                                  parameters: dict, content: str | None) -> dict | None:
        selected = parameters.get('masscube_config_path')
        # Existing API clients can still explicitly supply native JSON overrides.
        if content is None and not selected and 'masscube_parameters' in parameters:
            try:
                validate_masscube_parameters(parameters['masscube_parameters'], workflow.parameter_schema_json)
            except ValueError as exc:
                raise bad_request(str(exc)) from exc
            return None
        if parameters.get('masscube_parameters'):
            raise bad_request('Use the MassCube template or masscube_parameters JSON, not both')
        selected = selected or (workflow.static_input_config_json or {}).get('masscube_config_path')
        if not selected:
            raise bad_request('Select a MassCube parameter template')
        relative = self.validate_masscube_config_path(workflow, str(selected))
        if content is None:
            content = self.get_masscube_template_content(user_id, workflow.id, relative)['content']
        try:
            parameters['masscube_parameters'] = parse_masscube_template(content, workflow.parameter_schema_json)
        except ValueError as exc:
            raise bad_request(str(exc)) from exc
        parameters['masscube_config_path'] = relative
        return {'source_path': relative, 'content': content,
                'checksum_sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()}

    def get_msdial_template_content(self, user_id: int, workflow_definition_id: int, value: str) -> dict:
        if not user_id:
            raise bad_request("Authenticated user is required")
        workflow = self.active_definition(workflow_definition_id)
        relative_path = self.validate_msdial_config_path(workflow, value)
        template_path = self.resolve_pipeline_root(workflow) / relative_path
        try:
            if template_path.stat().st_size > self.MAX_MSDIAL_TEMPLATE_BYTES:
                raise bad_request(f"MS-DIAL parameter template must be at most {self.MAX_MSDIAL_TEMPLATE_BYTES} bytes")
            template_bytes = template_path.read_bytes()
            content = template_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise bad_request("MS-DIAL parameter template must be UTF-8 text") from exc
        except OSError as exc:
            raise bad_request(f"Unable to read MS-DIAL parameter template: {value}") from exc
        self.validate_msdial_template_text(content)
        return {
            "path": relative_path,
            "content": content,
            "checksum_sha256": hashlib.sha256(template_bytes).hexdigest(),
        }

    @classmethod
    def msdial_template_snapshot(cls, source_path: str, content: str) -> dict:
        cls.validate_msdial_template_text(content)
        return {
            "source_path": source_path,
            "content": content,
            "checksum_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }

    @classmethod
    def validate_msdial_template_text(cls, content: str) -> None:
        if not content.strip():
            raise bad_request("MS-DIAL parameter template cannot be empty")
        if len(content.encode("utf-8")) > cls.MAX_MSDIAL_TEMPLATE_BYTES:
            raise bad_request(f"MS-DIAL parameter template must be at most {cls.MAX_MSDIAL_TEMPLATE_BYTES} bytes")
        if not cls.is_msdial_template_text(content):
            raise bad_request("MS-DIAL parameter template is missing required MS-DIAL fields")

    def active_definition(self, workflow_definition_id: int) -> WorkflowDefinition:
        workflow = self.db.query(WorkflowDefinition).filter(WorkflowDefinition.id == workflow_definition_id).first()
        if not workflow or not workflow.is_active:
            raise not_found("Workflow definition not found or inactive")
        return workflow

    def validate_library_parameters(self, workflow: WorkflowDefinition, parameters: dict) -> None:
        for key, suffix, label in (
            ("ms1_library_path", ".txt", "MS1 library"),
            ("ms2_library_path", ".msp", "MS2 library"),
            ("is_library_path", ".txt", "IS library"),
        ):
            selected_value = parameters.get(key)
            if selected_value in (None, ""):
                continue
            parameters[key] = self.validate_library_path(workflow, str(selected_value), suffix, label)
        selected_template = parameters.get("msdial_config_path")
        if selected_template not in (None, ""):
            parameters["msdial_config_path"] = self.validate_msdial_config_path(workflow, str(selected_template))

    def validate_library_path(self, workflow: WorkflowDefinition, value: str, suffix: str, label: str) -> str:
        pipeline_root = self.resolve_pipeline_root(workflow)
        library_root = (pipeline_root / "libraries").resolve()
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = pipeline_root / candidate
        resolved = candidate.resolve()

        try:
            resolved.relative_to(library_root)
        except ValueError as exc:
            raise bad_request(f"{label} must be inside the workflow libraries folder") from exc

        if resolved.suffix.lower() != suffix:
            raise bad_request(f"{label} must use the {suffix} extension")
        if not resolved.is_file():
            raise bad_request(f"{label} does not exist: {value}")
        return resolved.relative_to(pipeline_root).as_posix()

    def validate_msdial_config_path(self, workflow: WorkflowDefinition, value: str) -> str:
        pipeline_root = self.resolve_pipeline_root(workflow)
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = pipeline_root / candidate
        resolved = candidate.resolve()

        if resolved.suffix.lower() != ".txt":
            raise bad_request("MS-DIAL parameter template must use the .txt extension")
        if not resolved.is_file():
            raise bad_request(f"MS-DIAL parameter template does not exist: {value}")

        static_inputs = dict(workflow.static_input_config_json or {})
        default_value = static_inputs.get("msdial_config_path")
        default_resolved = None
        if default_value:
            default_candidate = Path(str(default_value)).expanduser()
            if not default_candidate.is_absolute():
                default_candidate = pipeline_root / default_candidate
            default_resolved = default_candidate.resolve()

        template_root = (pipeline_root / "templates" / "msdial").resolve()
        default_template_dir = default_resolved.parent if default_resolved is not None else None
        if default_resolved is not None and resolved == default_resolved:
            return resolved.relative_to(pipeline_root).as_posix()
        try:
            resolved.relative_to(template_root)
            return resolved.relative_to(pipeline_root).as_posix()
        except ValueError:
            pass
        if default_template_dir is not None:
            try:
                resolved.relative_to(default_template_dir)
            except ValueError:
                pass
            else:
                if self.is_msdial_template_file(resolved):
                    return resolved.relative_to(pipeline_root).as_posix()
        raise bad_request("MS-DIAL parameter template must be the workflow default, inside templates/msdial, or beside the default template")

    def resolve_pipeline_root(self, workflow: WorkflowDefinition) -> Path:
        root_value = workflow.pipeline_root or self.settings.workflow_pipeline_root
        root = Path(str(root_value)).expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
        return root.resolve()

    def resolve_library_root(self, workflow: WorkflowDefinition) -> Path:
        return self.resolve_pipeline_root(workflow) / "libraries"

    @staticmethod
    def scan_library_options(library_root: Path, suffix: str) -> list[dict]:
        if not library_root.exists():
            return []
        pipeline_root = library_root.parent.resolve()
        options = []
        for candidate in sorted(library_root.rglob(f"*{suffix}")):
            if not candidate.is_file():
                continue
            relative_value = candidate.resolve().relative_to(pipeline_root).as_posix()
            label = candidate.relative_to(library_root).as_posix()
            options.append({"label": label, "value": relative_value})
        return options

    @staticmethod
    def scan_msdial_template_options(pipeline_root: Path, default_value: str | None) -> list[dict]:
        options: list[dict] = []
        seen_values: set[str] = set()
        default_path: Path | None = None
        if default_value:
            default_path = Path(default_value).expanduser()
            if not default_path.is_absolute():
                default_path = pipeline_root / default_path
            if default_path.resolve().is_file():
                normalized = default_path.resolve().relative_to(pipeline_root.resolve()).as_posix()
                options.append({"label": f"Default - {normalized}", "value": normalized})
                seen_values.add(normalized)

        template_root = pipeline_root / "templates" / "msdial"
        if not template_root.exists():
            template_candidates: list[Path] = []
        else:
            template_candidates = sorted(template_root.rglob("*.txt"))

        default_dir_candidates: list[Path] = []
        if default_path is not None and default_path.parent.exists():
            default_dir_candidates = sorted(default_path.parent.glob("*.txt"))

        for candidate in template_candidates:
            if not candidate.is_file():
                continue
            relative_value = candidate.resolve().relative_to(pipeline_root.resolve()).as_posix()
            if relative_value in seen_values:
                continue
            label = candidate.relative_to(template_root).as_posix()
            options.append({"label": label, "value": relative_value})
            seen_values.add(relative_value)
        for candidate in default_dir_candidates:
            if not candidate.is_file() or not WorkflowDefinitionService.is_msdial_template_file(candidate):
                continue
            relative_value = candidate.resolve().relative_to(pipeline_root.resolve()).as_posix()
            if relative_value in seen_values:
                continue
            label = candidate.relative_to(default_path.parent if default_path is not None else pipeline_root).as_posix()
            options.append({"label": label, "value": relative_value})
            seen_values.add(relative_value)
        return options

    @staticmethod
    def is_msdial_template_file(candidate: Path) -> bool:
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        return WorkflowDefinitionService.is_msdial_template_text(text)

    @staticmethod
    def is_msdial_template_text(text: str) -> bool:
        if "MS-DIAL" not in text:
            return False
        lines = text.splitlines()
        has_ms2 = WorkflowDefinitionService._has_msdial_label(lines, ("MSP file path", "MSP file"))
        has_ms1 = WorkflowDefinitionService._has_msdial_label(lines, ("Text DB file path", "Text file"))
        has_reference = WorkflowDefinitionService._has_msdial_label(
            lines,
            ("Alignment reference file ID", "Alignment reference file name", "Reference file"),
        )
        return has_ms1 and has_ms2 and has_reference

    @staticmethod
    def _has_msdial_label(lines: list[str], labels: tuple[str, ...]) -> bool:
        return any(
            line.startswith(f"{label}:") or line.startswith(f"{label}\t")
            for line in lines
            for label in labels
        )

    @staticmethod
    def default_library_value(value: str | None) -> str | None:
        if not value:
            return None
        normalized = str(value).strip()
        return normalized if normalized.startswith("libraries/") else None

    @staticmethod
    def default_template_value(value: str | None) -> str | None:
        if not value:
            return None
        return str(value).strip() or None
