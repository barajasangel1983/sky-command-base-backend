from datetime import timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from . import deps
from .deps import get_db, verify_password, hash_password, create_access_token
from .models import User as UserModel
from .schemas import LoginRequest, TokenResponse, User as UserSchema

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: Session = Depends(get_db)):
  user = db.query(UserModel).filter(UserModel.username == payload.username).first()
  if not user or not verify_password(payload.password, user.password_hash):
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

  access_token_expires = timedelta(minutes=deps.ACCESS_TOKEN_EXPIRE_MINUTES)
  token = create_access_token(data={"sub": user.username}, expires_delta=access_token_expires)

  return TokenResponse(
    token=token,
    user=UserSchema(
      id=user.id,
      username=user.username,
      role=user.role,  # type: ignore[arg-type]
      status=user.status,  # type: ignore[arg-type]
      createdAt=user.created_at.strftime("%Y-%m-%d"),
    ),
  )


@router.get("/me", response_model=UserSchema)
async def me(current_user: UserSchema = Depends(deps.get_current_user)):
  return current_user
