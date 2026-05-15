from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.auth.schemas import LoginRequest, TokenResponse, UserOut
from app.auth.security import verify_password, hash_password, create_access_token, get_current_user
from app.auth.database import get_db, sync_user_role
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest):
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM users WHERE username = ? OR email = ?",
        (body.username, body.email),
    ).fetchone()
    if existing is not None:
        conn.close()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username or email already exists")

    hashed = hash_password(body.password)
    conn.execute(
        "INSERT INTO users (username, email, hashed_password) VALUES (?, ?, ?)",
        (body.username, body.email, hashed),
    )
    role = sync_user_role(conn, body.email, settings.admin_emails_set())
    conn.commit()
    conn.close()

    token = create_access_token(data={"sub": body.username, "role": role})
    return TokenResponse(access_token=token, username=body.username)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    conn = get_db()
    user = conn.execute(
        "SELECT id, username, email, hashed_password, is_active FROM users WHERE username = ?",
        (body.username,),
    ).fetchone()
    conn.close()

    if user is None or not verify_password(body.password, user["hashed_password"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    conn = get_db()
    role = sync_user_role(conn, user["email"], settings.admin_emails_set())
    conn.commit()
    conn.close()

    token = create_access_token(data={"sub": user["username"], "role": role})
    return TokenResponse(access_token=token, username=user["username"])


@router.get("/me", response_model=UserOut)
def me(current_user: dict = Depends(get_current_user)):
    return current_user


@router.get("/google")
def google_login():
    """Redirect the browser to Google's OAuth consent screen."""
    if not settings.google_client_id:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Google SSO is not configured")

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
    }
    return RedirectResponse(url=f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@router.get("/google/callback")
async def google_callback(code: str):
    """Handle Google OAuth callback: verify domain, upsert user, return JWT."""
    if not settings.google_client_id:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Google SSO is not configured")

    # Exchange authorization code for tokens
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )

    if token_resp.status_code != 200:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to exchange code with Google")

    access_token = token_resp.json().get("access_token")

    # Fetch user info from Google
    async with httpx.AsyncClient() as client:
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if userinfo_resp.status_code != 200:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to fetch user info from Google")

    userinfo = userinfo_resp.json()
    email: str = userinfo.get("email", "")
    google_id: str = userinfo.get("sub", "")

    # Enforce Spectrum Groupe domain restriction
    if not email.endswith(f"@{settings.allowed_email_domain}"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access restricted to @{settings.allowed_email_domain} accounts",
        )

    # Upsert user: create on first login, update google_id if needed
    conn = get_db()
    user = conn.execute("SELECT id, username, is_active FROM users WHERE email = ?", (email,)).fetchone()

    if user is None:
        username = email.split("@")[0]
        # Ensure username uniqueness
        base = username
        suffix = 1
        while conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            username = f"{base}{suffix}"
            suffix += 1

        conn.execute(
            "INSERT INTO users (username, email, hashed_password, google_id) VALUES (?, ?, NULL, ?)",
            (username, email, google_id),
        )
        conn.commit()
        user = conn.execute("SELECT id, username, is_active FROM users WHERE email = ?", (email,)).fetchone()
    else:
        # Update google_id if not set yet
        conn.execute("UPDATE users SET google_id = ? WHERE email = ? AND google_id IS NULL", (google_id, email))
        conn.commit()

    role = sync_user_role(conn, email, settings.admin_emails_set())
    conn.commit()
    conn.close()

    if not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    jwt_token = create_access_token(data={"sub": user["username"], "role": role})
    redirect_url = f"{settings.frontend_origin}/auth/callback?token={jwt_token}&username={user['username']}"
    return RedirectResponse(url=redirect_url)
