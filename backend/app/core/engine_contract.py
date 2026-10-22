"""Versioned, data-only execution descriptions shared by the API and worker."""
from copy import deepcopy
import hashlib
from pathlib import Path
import re
from typing import Literal

from jsonschema import Draft202012Validator, ValidationError
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CurationRule(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    rule_id: str = Field(min_length=1)
    params: dict


class CurationProfile(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str
    statistics: list[CurationRule]
    annotation: list[CurationRule]


def validate_curation_rule_selection(profiles: dict, profile: str, selection: dict) -> dict:
    """Validate identifiers only; scientific evaluation remains in the package."""
    if not isinstance(selection, dict) or set(selection) - {'statistics', 'annotation'}:
        raise ValueError('enabled_rules must contain only statistics and/or annotation arrays')
    if profile not in profiles:
        raise ValueError('enabled_rules requires a supported profile')
    normalized = {}
    for domain, ids in selection.items():
        available = [rule['rule_id'] for rule in profiles[profile][domain]]
        if (not isinstance(ids, list) or any(not isinstance(rule, str) for rule in ids)
                or len(set(ids)) != len(ids) or set(ids) - set(available)):
            raise ValueError(f'enabled_rules.{domain} must contain unique rule IDs from the selected profile')
        normalized[domain] = [rule for rule in available if rule in ids]
    return normalized


class PackageContract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: str
    commit: str = Field(pattern=r'^[0-9a-f]{40}$')
    container_image: str
    config: dict
    curation_profiles: dict[str, CurationProfile] = Field(default_factory=dict)
    supports_rule_selection: bool = False

    @model_validator(mode='after')
    def valid_profile_catalog(self):
        if self.curation_profiles:
            if self.config.get('profile') not in self.curation_profiles:
                raise ValueError('Default package profile is missing from the profile catalog')
            if any(name != profile.name for name, profile in self.curation_profiles.items()):
                raise ValueError('Package profile names must match their catalog keys')
        if self.supports_rule_selection and not self.curation_profiles:
            raise ValueError('Package rule selection requires a profile catalog')
        if 'enabled_rules' in self.config:
            if not self.supports_rule_selection:
                raise ValueError('This package image does not support rule selection')
            validate_curation_rule_selection(
                {key: profile.model_dump() for key, profile in self.curation_profiles.items()},
                self.config.get('profile'), self.config['enabled_rules'])
        return self


class EngineStage(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^[a-z][a-z0-9_-]*$')
    label: str
    patterns: list[str]

    @model_validator(mode='after')
    def valid_patterns(self):
        for pattern in self.patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f'Invalid stage process pattern: {pattern}') from exc
        return self


class EngineDescriptor(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal[1]
    engine_id: str = Field(pattern=r'^[a-z][a-z0-9_-]*$')
    engine_version: str
    adapter_id: str = Field(pattern=r'^[a-z][a-z0-9_-]*$')
    evidence_adapter_id: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_-]*$')
    importer_id: str
    entrypoint: str
    required_inputs: dict[str, str]
    input_extensions: list[str] = Field(default_factory=list)
    minimum_acquisitions: int = Field(default=1, ge=1)
    support_files: list[str] = Field(default_factory=list)
    capabilities: list[str]
    review_transitions: dict[str, str] = Field(default_factory=dict)
    stages: list[EngineStage] = Field(default_factory=list)
    execution_profiles: list[str]
    source_paths: list[str]
    container_images: dict[str, str]
    package: PackageContract

    @model_validator(mode='after')
    def immutable_images(self):
        if any(not re.fullmatch(r'\.[a-z0-9]+', extension) for extension in self.input_extensions):
            raise ValueError('Engine input extensions must be lowercase dotted suffixes')
        if len({stage.id for stage in self.stages}) != len(self.stages):
            raise ValueError('Engine stage IDs must be unique')
        for image in [self.package.container_image, *self.container_images.values()]:
            if not re.fullmatch(r'(?:[^\s]+@)?sha256:[0-9a-f]{64}', image):
                raise ValueError('Engine containers must use immutable repository@sha256 references or full local sha256 IDs')
        if self.package.config.get('source_tool') != self.importer_id:
            raise ValueError('Package source_tool must match the engine importer')
        return self


def validate_engine_acquisitions(descriptor: dict, files: list) -> None:
    """Apply the selected engine's input limits before creating a queued job."""
    minimum = descriptor.get('minimum_acquisitions', 1)
    if len(files) < minimum:
        raise ValueError(f'Engine requires at least {minimum} acquisition files')
    extensions = descriptor.get('input_extensions', [])
    unsupported = [file.original_filename for file in files
                   if extensions and Path(file.original_filename).suffix.lower() not in extensions]
    if unsupported:
        raise ValueError(f'Engine received unsupported acquisition files: {", ".join(unsupported)}')


def validate_engine_parameters(schema: dict, values: dict) -> dict:
    """Use the workflow's existing JSON Schema as the single parameter definition."""
    Draft202012Validator.check_schema(schema)
    normalized = deepcopy(values)
    for key, definition in schema.get('properties', {}).items():
        if key not in normalized and 'default' in definition:
            normalized[key] = deepcopy(definition['default'])
    try:
        Draft202012Validator(schema).validate(normalized)
    except ValidationError as exc:
        raise ValueError(f'Invalid engine parameters: {exc.message}') from exc
    return normalized


def sha256_file(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def contained_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Engine source must be relative to the workflow: {relative}')
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f'Engine source escapes workflow directory: {relative}')
    return resolved


def snapshot_engine(workflow, parameters: dict, pipeline_root: Path, metabolensr_profile: str | None = None,
                    metabolensr_enabled_rules: dict | None = None,
                    metabolensr_expected_image: str | None = None) -> dict:
    static = workflow.static_input_config_json or {}
    descriptor = EngineDescriptor.model_validate(static['engine']).model_dump()
    if (metabolensr_expected_image is not None
            and metabolensr_expected_image != descriptor['package']['container_image']):
        raise ValueError('The MetaboLensR package changed. Refresh the catalog and check its rules before launching.')
    if metabolensr_profile is not None:
        if metabolensr_profile not in descriptor['package']['curation_profiles']:
            raise ValueError(f'Unsupported MetaboLensR profile: {metabolensr_profile}')
        descriptor['package']['config']['profile'] = metabolensr_profile
    package = descriptor['package']
    if metabolensr_enabled_rules is not None:
        if not package['supports_rule_selection']:
            raise ValueError('This package image does not support rule selection')
        package['config']['enabled_rules'] = validate_curation_rule_selection(
            package['curation_profiles'], package['config']['profile'], metabolensr_enabled_rules)
    else:
        package['config'].pop('enabled_rules', None)
    # Validate the resulting frozen config too, including descriptor defaults.
    PackageContract.model_validate(package)
    schema = deepcopy(workflow.parameter_schema_json)
    normalized = validate_engine_parameters(schema, parameters)
    for source, target in descriptor['review_transitions'].items():
        for stage in (source, target):
            validate_engine_parameters(schema, {**normalized, 'workflow_stage': stage})
    for key, label in descriptor['required_inputs'].items():
        if normalized.get(key) in (None, '') and static.get(key) in (None, ''):
            raise ValueError(f'Missing required engine input: {label}')
    sources = {}
    for relative in descriptor['source_paths']:
        path = contained_path(pipeline_root, relative)
        candidates = sorted(path.rglob('*')) if path.is_dir() else [path]
        for candidate in candidates:
            if candidate.is_dir() or '__pycache__' in candidate.parts or candidate.suffix == '.pyc':
                continue
            key = candidate.relative_to(pipeline_root).as_posix()
            candidate = contained_path(pipeline_root, key)
            sources[key] = sha256_file(candidate)
    if descriptor['entrypoint'] not in sources:
        raise ValueError('Engine entrypoint must be included in source_paths')
    support = {}
    for key in descriptor['support_files']:
        value = normalized.get(key) or static.get(key)
        if value:
            relative = Path(str(value))
            path = contained_path(pipeline_root, str(relative))
            support[key] = {'path': relative.as_posix(), 'sha256': sha256_file(path)}
    return {'descriptor': descriptor, 'parameters': normalized, 'parameter_schema': schema,
            'source_sha256': sources, 'support_files': support}
