"""Auth endpoints: POST /api/auth/register and POST /api/auth/login."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.schemas import AuthResponse, LoginRequest, RegisterRequest, UserOut
from app.security import create_token, hash_password, verify_password

router = APIRouter()


def _auth_response(user: User) -> AuthResponse:
    return AuthResponse(
        access_token=create_token(user.id),
        token_type="bearer",
        user=UserOut(id=user.id, email=user.email),
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> AuthResponse:
    email = (body.email or "").strip()
    if not email:
        raise HTTPException(status_code=422, detail="Email is required")
    if not body.password:
        raise HTTPException(status_code=422, detail="Password is required")

    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Email already registered")

    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return _auth_response(user)


@router.post("/login", response_model=AuthResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> AuthResponse:
    user = db.scalar(select(User).where(User.email == (body.email or "").strip()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid credentials")
    return _auth_response(user)
