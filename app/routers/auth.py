from fastapi import APIRouter, HTTPException, Depends, status

from app.auth.schemas import LoginRequest, TokenResponse, UserOut
from app.auth.security import verify_password, create_access_token, get_current_user
from app.auth.database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


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
