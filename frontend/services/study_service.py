from services.api_client import call_api


def draft_required_message(status: str) -> str:
    return f"Action blocked: study status is '{status}'. Samples, metadata, and files are editable only in 'draft'."


def create_study(token: str, title: str | None, description: str | None) -> dict:
    return call_api(
        "POST",
        "/studies",
        token=token,
        payload={"title": title or "Untitled", "description": description},
    )


def list_studies(token: str) -> list[dict]:
    return call_api("GET", "/studies", token=token)


def get_study(token: str, study_id: int) -> dict:
    return call_api("GET", f"/studies/{study_id}", token=token)


def get_study_status(token: str, study_id: int) -> str:
    study = get_study(token, study_id)
    return study.get("status", "unknown")


def ensure_draft_study(token: str, study_id: int) -> None:
    status = get_study_status(token, study_id)
    if status != "draft":
        raise ValueError(draft_required_message(status))


def ensure_sample_details_editable(token: str, study_id: int) -> None:
    """Sample names and characteristics stay correctable after a run; only archived Studies are frozen."""
    status = get_study_status(token, study_id)
    if status == "archived":
        raise ValueError("Action blocked: archived Studies are read-only.")


def submit_study(token: str, study_id: int) -> dict:
    return call_api("POST", f"/studies/{study_id}/submit", token=token)


def reopen_study(token: str, study_id: int) -> dict:
    return call_api("POST", f"/studies/{study_id}/reopen", token=token)


def transition_study(token: str, study_id: int, transition_status: str | None) -> dict:
    if not transition_status:
        raise ValueError("Transition target is required")
    return call_api("POST", f"/studies/{study_id}/transition/{transition_status}", token=token)


def list_study_assays(token: str, study_id: int) -> list[dict]:
    return call_api("GET", f"/studies/{study_id}/assays", token=token)


def create_study_assay(token: str, study_id: int, name: str | None, metabolomics_assay_type: str | None = None) -> dict:
    ensure_draft_study(token, study_id)
    payload = {
        "name": (name or "").strip() or None,
        "platform": "metabolomics",
        "metabolomics_assay_type": (metabolomics_assay_type or "").strip() or "LC_MS",
    }
    return call_api("POST", f"/studies/{study_id}/assays", token=token, payload=payload)
