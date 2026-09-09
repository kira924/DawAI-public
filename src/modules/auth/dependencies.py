from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.core.security import decode_access_token
from src.modules.auth.principal import AuthenticatedPrincipal
from src.modules.auth.service import get_active_session, is_account_enabled
from src.modules.auth.support import activate_support_context
from src.modules.users import crud

# 1. Define the OAuth2 scheme and point it to our login endpoint
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# 2. The Dependency Guard function
def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    support_grant_id: str | None = Header(default=None, alias="X-Support-Grant-ID"),
    db: Session = Depends(get_db),
) -> AuthenticatedPrincipal:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
        user_id = int(str(payload["sub"]))
        session_id = str(payload["sid"])
        if not session_id:
            raise credentials_exception
    except (InvalidTokenError, KeyError, TypeError, ValueError):
        raise credentials_exception

    session = get_active_session(db, session_id=session_id, user_id=user_id)
    if session is None:
        raise credentials_exception

    user = crud.get_user(db, user_id=user_id)
    if user is None or not is_account_enabled(user):
        raise credentials_exception

    if support_grant_id is not None:
        return activate_support_context(db, user, session.id, support_grant_id, request)

    return AuthenticatedPrincipal(
        id=user.id,
        auth_user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        tenant_id=user.tenant_id,
        session_id=session.id,
    )
