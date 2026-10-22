import hashlib
import time


def _auth_fingerprint(token: str | None) -> str | None:
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def build_page_cache(data, token, *, study_id=None, assay_id=None, loaded_at=None) -> dict:
    return {
        "context": {
            "auth": _auth_fingerprint(token),
            "study_id": study_id,
            "assay_id": assay_id,
        },
        "data": data,
        "loaded_at": time.time() if loaded_at is None else loaded_at,
    }


def page_cache_matches(cache, token, *, study_id=None, assay_id=None) -> bool:
    if not isinstance(cache, dict) or "data" not in cache:
        return False
    return cache.get("context") == {
        "auth": _auth_fingerprint(token),
        "study_id": study_id,
        "assay_id": assay_id,
    }


def page_cache_data(cache, default=None):
    if not isinstance(cache, dict) or "data" not in cache:
        return default
    return cache["data"]


def should_reuse_page_cache(
    cache,
    token,
    *,
    triggered_id,
    passive_trigger_ids,
    study_id=None,
    assay_id=None,
) -> bool:
    return triggered_id in set(passive_trigger_ids) and page_cache_matches(
        cache,
        token,
        study_id=study_id,
        assay_id=assay_id,
    )
