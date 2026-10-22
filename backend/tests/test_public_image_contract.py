import json
from pathlib import Path

import pytest

from app.core.engine_contract import EngineDescriptor


DESCRIPTOR = Path(__file__).resolve().parents[2] / "workflows/nextflow4ms-dial-msdial5-clean/engines/msdial5/engine.json"


def test_local_image_identity_is_immutable_but_tags_are_rejected():
    descriptor = json.loads(DESCRIPTOR.read_text())
    descriptor["package"]["container_image"] = "sha256:" + "a" * 64
    descriptor["container_images"] = {"engine": "sha256:" + "b" * 64}
    EngineDescriptor.model_validate(descriptor)
    for unsafe in ("public:latest", "sha256:abc", "sha256:" + "A" * 64, "sha256:" + "a" * 64 + "\n"):
        descriptor["container_images"]["engine"] = unsafe
        with pytest.raises(ValueError):
            EngineDescriptor.model_validate(descriptor)
