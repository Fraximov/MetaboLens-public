from services.api_client import call_api


def login(email: str | None, password: str | None) -> str:
    data = call_api("POST", "/auth/login", payload={"email": email, "password": password})
    return data["access_token"]
