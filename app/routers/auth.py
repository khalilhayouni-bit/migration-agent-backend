from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel

from app.auth.schemas import LoginRequest, TokenResponse, UserOut
from app.auth.security import verify_password, hash_password, create_access_token, get_current_user
from app.auth.database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


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
    conn.commit()
    conn.close()

    token = create_access_token(data={"sub": body.username})
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

    token = create_access_token(data={"sub": user["username"]})
    return TokenResponse(access_token=token, username=user["username"])


@router.get("/me", response_model=UserOut)
def me(current_user: dict = Depends(get_current_user)):
    return current_user
