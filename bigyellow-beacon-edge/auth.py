"""
Edge Authentication Dependencies and Role Guards
"""

from typing import Dict, Any, Optional, Callable
from fastapi import Request, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from db import get_db
from security import decode_edge_session_token
from contract.enums import Role

http_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_edge_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer_scheme),
) -> Dict[str, Any]:
    auth_header = request.headers.get("authorization")
    token: Optional[str] = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    elif "token" in request.query_params:
        token = request.query_params["token"]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Missing or invalid Authorization header or token query parameter"}
        )
    payload = decode_edge_session_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid or expired session token"}
        )

    return payload


def require_edge_role(*roles: str) -> Callable:
    async def role_checker(user: Dict[str, Any] = Depends(get_current_edge_user)) -> Dict[str, Any]:
        user_role = user.get("role")
        if user_role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "Forbidden",
                    "message": f"Endpoint requires one of roles: [{', '.join(roles)}]. Current role: {user_role}",
                }
            )
        return user
    return role_checker
