from pydantic import BaseModel, Field


class WalkPingRequest(BaseModel):
    init_data: str
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class AdminConsoleRequest(BaseModel):
    init_data: str
    command: str


class UserRequest(BaseModel):
    init_data: str
