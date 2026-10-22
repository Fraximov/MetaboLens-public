"""Resolve declared adapters without importing code named by a workflow artifact."""
from app.core.errors import bad_request
from app.services.engine_evidence.masscube import MassCubeEvidence


class MsdialEvidence:
    def __init__(self, eic):
        self.eic = eic

    def features(self, bundle):
        return self.eic.package_evidence_features(bundle)

    def trace(self, bundle, source_feature_id, samples, *, evidence_features):
        return self.eic.package_feature_evidence(
            bundle, source_feature_id, samples, evidence_features=evidence_features)


ADAPTERS = {'msdial-aef-v1': MsdialEvidence, 'masscube-indexed-v1': MassCubeEvidence}
LEGACY_ADAPTERS = {'msdial': 'msdial-aef-v1'}


def resolve(bundle, eic):
    descriptor = bundle.get('engine_descriptor') or {}
    source = (bundle.get('manifest') or {}).get('source') or {}
    adapter_id = descriptor.get('evidence_adapter_id')
    if adapter_id is None:
        adapter_id = source.get('evidence_adapter_id')
    if adapter_id is None:
        adapter_id = LEGACY_ADAPTERS.get(bundle.get('source_tool'))
    if adapter_id is None:
        return None
    if not isinstance(adapter_id, str) or adapter_id not in ADAPTERS:
        raise bad_request('Unknown declared native evidence adapter')
    return ADAPTERS[adapter_id](eic)
