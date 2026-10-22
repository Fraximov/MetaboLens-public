from services.api_client import call_api


def list_study_files(token: str, study_id: int) -> list[dict]:
    return call_api("GET", f"/studies/{study_id}/files", token=token)


def list_assay_files(
    token: str,
    assay_id: int,
    *,
    workflow_inputs_only: bool = False,
) -> list[dict]:
    path = f"/assays/{assay_id}/files"
    if workflow_inputs_only:
        path += "?workflow_inputs_only=true"
    return call_api("GET", path, token=token)


list_files = list_study_files
