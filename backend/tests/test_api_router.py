from app.api.router import api_router


def test_router_does_not_mount_legacy_experiments_prefix() -> None:
    mounted_paths = {route.path for route in api_router.routes}

    assert "/studies" in mounted_paths
    assert "/experiments" not in mounted_paths


def test_router_mounts_workflow_job_delete_endpoint() -> None:
    assert any(
        route.path == "/assays/{assay_id}/workflow-jobs/{job_id}"
        and "DELETE" in (route.methods or set())
        for route in api_router.routes
    )
