from services.api_client import call_api


def get_study_summary(token: str, study_id: int | None) -> dict:
    if not study_id:
        raise ValueError("Select study")
    return call_api("GET", f"/studies/{study_id}/summary", token=token)


def list_study_activity(token: str, study_id: int | None, limit: int = 100) -> list[dict]:
    if not study_id:
        raise ValueError("Select study")
    safe_limit = max(1, min(int(limit), 500))
    return call_api("GET", f"/studies/{study_id}/activity?limit={safe_limit}", token=token)


def list_study_assays(token: str, study_id: int | None) -> list[dict]:
    if not study_id:
        raise ValueError("Select study")
    return call_api("GET", f"/studies/{study_id}/assays", token=token)


def get_study_structure(token: str, study_id: int | None) -> dict:
    if not study_id:
        raise ValueError("Select study")
    return call_api("GET", f"/studies/{study_id}/structure", token=token)


get_experiment_summary = get_study_summary
list_experiment_activity = list_study_activity
