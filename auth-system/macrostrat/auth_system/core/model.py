from pydantic import BaseModel


class TokenData(BaseModel):
    sub: str
    groups: list[int] = []


class User(BaseModel):
    username: str
    email: str | None = None
    full_name: str | None = None
    disabled: bool | None = None


class AccessToken(BaseModel):
    group: int
    token: str


class GroupTokenRequest(BaseModel):
    expiration: int
    group_id: int
