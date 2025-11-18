"""
Authentication dependencies using HTTP Basic Auth with htpasswd.

Replaces Flask-HtPasswd with FastAPI dependency injection.
"""

import os
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from passlib.apache import HtpasswdFile
from functools import lru_cache
import secrets

security = HTTPBasic()


@lru_cache()
def get_htpasswd_file() -> HtpasswdFile:
    """
    Load htpasswd file (cached).

    The file path is read from the AUTH_KEY environment variable.
    """
    auth_key_path = os.getenv('AUTH_KEY')
    if not auth_key_path:
        raise ValueError("AUTH_KEY environment variable not set")

    if not os.path.exists(auth_key_path):
        raise FileNotFoundError(f"htpasswd file not found: {auth_key_path}")

    return HtpasswdFile(auth_key_path)


async def verify_credentials(
    credentials: HTTPBasicCredentials = Depends(security)
) -> str:
    """
    Verify HTTP Basic Auth credentials against htpasswd file.

    Args:
        credentials: HTTP Basic Auth credentials from request

    Returns:
        Username if authentication successful

    Raises:
        HTTPException: 401 if authentication fails
    """
    try:
        htpasswd = get_htpasswd_file()
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Authentication configuration error: {str(e)}"
        )

    # Timing-attack resistant comparison
    username = credentials.username
    password = credentials.password

    # Check if user exists and password is correct
    if not htpasswd.check_password(username, password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    return username


# Alias for clarity in route definitions
get_current_user = verify_credentials
