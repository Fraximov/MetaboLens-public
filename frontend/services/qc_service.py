from services.api_client import call_api


def load_example_qc_report(token: str) -> dict:
    return call_api("GET", "/qc/example-mzml", token=token)
