import os
from typing import Annotated, Optional

import bcrypt
from fastapi import Depends, HTTPException, Request
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
    OAuth2AuthorizationCodeBearer,
)
from fastapi.security.utils import get_authorization_scheme_param
from jose import JWTError, jwt
from starlette.status import HTTP_401_UNAUTHORIZED

from .database import get_access_token
from .model import TokenData

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours
GROUP_TOKEN_LENGTH = 32
GROUP_TOKEN_SALT = (
    b"$2b$12$yQrslvQGWDFjwmDBMURAUe"  # Hardcode salt so hashes are consistent
)


class OAuth2AuthorizationCodeBearerWithCookie(OAuth2AuthorizationCodeBearer):
    """Tweak FastAPI's OAuth2AuthorizationCodeBearer to use a cookie instead of a header"""

    async def __call__(self, request: Request) -> Optional[str]:
        authorization = request.cookies.get(
            "Authorization"
        )  # authorization = request.headers.get("Authorization")
        scheme, param = get_authorization_scheme_param(authorization)
        if not authorization or scheme.lower() != "bearer":
            if self.auto_error:
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED,
                    detail="Not authenticated",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            else:
                return None  # pragma: nocover
        return param


oauth2_scheme = OAuth2AuthorizationCodeBearerWithCookie(
    authorizationUrl="/security/login", tokenUrl="/security/callback", auto_error=False
)

http_bearer = HTTPBearer(auto_error=False)


def get_groups_from_header_token(
    header_token: Annotated[HTTPAuthorizationCredentials, Depends(http_bearer)]
) -> int | None:
    """Get the groups from the bearer token in the header"""

    if header_token is None:
        return None

    token_hash = bcrypt.hashpw(header_token.credentials.encode(), GROUP_TOKEN_SALT)
    token_hash_string = token_hash.decode("utf-8")

    token = get_access_token(token=token_hash_string)

    if token is None:
        return None

    return token.group


def get_user_token_from_cookie(token: Annotated[str | None, Depends(oauth2_scheme)]):
    """Get the current user from the JWT token in the cookies"""

    # If there wasn't a token include in the request
    if token is None:
        return None

    try:
        payload = jwt.decode(
            token,
            os.environ["SECRET_KEY"],
            algorithms=[os.environ["JWT_ENCRYPTION_ALGORITHM"]],
        )
        sub: str = payload.get("sub")
        groups = payload.get("groups", [])
        token_data = TokenData(sub=sub, groups=groups)
    except JWTError as e:
        return None

    return token_data


def get_groups(
    user_token_data: TokenData | None = Depends(get_user_token_from_cookie),
    header_token: int | None = Depends(get_groups_from_header_token),
) -> list[int]:
    """Get the groups from both the cookies and header"""

    groups = []
    if user_token_data is not None:
        groups = user_token_data.groups

    if header_token is not None:
        groups.append(header_token)

    return groups


def get_user_id(
    user_token_data: TokenData | None = Depends(get_user_token_from_cookie),
) -> str | None:
    """Get the user id from the cookies"""

    if user_token_data is None:
        return None

    return user_token_data.sub


def has_access(groups: list[int] = Depends(get_groups)) -> bool:
    """Check if the user has access to the group"""

    if "ENVIRONMENT" in os.environ and os.environ["ENVIRONMENT"] == "development":
        return True

    return 1 in groups
