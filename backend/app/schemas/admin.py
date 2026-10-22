from pydantic import BaseModel


class RoleAssignmentCreate(BaseModel):
    user_id: int
    role: str
    study_id: int | None = None
