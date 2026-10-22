from pydantic import BaseModel

from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(ORMModel):
    id: int
    email: str
    display_name: str
    is_active: bool
