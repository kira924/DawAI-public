from dataclasses import dataclass


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    id: int
    auth_user_id: int
    email: str
    full_name: str
    role: str
    tenant_id: int
    session_id: str
    support_grant_id: str | None = None
    support_scope: str | None = None

    @property
    def is_support_session(self) -> bool:
        return self.support_grant_id is not None
