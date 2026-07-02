from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Single role for now; the column exists so RBAC can land without a migration.
UserRole = Literal["admin"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SessionClaims(BaseModel):
    """Decoded session-JWT claims that the auth policy checks (sub + token version)."""

    sub: str
    ver: int


class UserRead(BaseModel):
    id: str
    username: str
    role: UserRole


class UserRecord(BaseModel):
    """Internal user row incl. the password hash (core → services only; never to api/)."""

    id: str
    username: str
    password_hash: str
    role: UserRole
    # Session-revocation counter (JWT ``ver`` claim); bumped on logout.
    token_version: int = 0

    def to_read(self) -> UserRead:
        return UserRead(id=self.id, username=self.username, role=self.role)
