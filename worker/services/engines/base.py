"""What an engine adapter receives and returns."""
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EngineContext:
    descriptor: dict
    parameters: dict
    manifest: dict
    workspace: Any
    staged_files: list[dict]
    support_files: dict[str, Path]
    settings: Any = None


@dataclass
class EngineLaunch:
    parameters: dict
    artifacts: list[tuple[Path, str]] = field(default_factory=list)
    native_outputs: list[tuple[str, str]] = field(default_factory=list)
    legacy_fields: dict = field(default_factory=dict)
    resource_groups: dict[str, str] = field(default_factory=dict)
    # Launch parameters the adapter turned into files or engine settings itself;
    # they are not forwarded to Nextflow.
    consumed_parameters: tuple[str, ...] = ()


def checked_template_bytes(snapshot: dict, label: str) -> bytes:
    """Return a launch-time template snapshot after checking its SHA-256."""

    content = snapshot['content'].encode('utf-8')
    if hashlib.sha256(content).hexdigest() != snapshot['checksum_sha256']:
        raise ValueError(f'{label} checksum mismatch')
    return content
