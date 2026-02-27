from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .deps import get_db, hash_password, get_current_admin
from .models import User as UserModel
from .schemas import User as UserSchema, UserCreate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/", response_model=List[UserSchema])
async def list_users(_: UserSchema = Depends(get_current_admin), db: Session = Depends(get_db)):
  users = db.query(UserModel).all()
  return [
    UserSchema(
      id=u.id,
      username=u.username,
      role=u.role,  # type: ignore[arg-type]
      status=u.status,  # type: ignore[arg-type]
      createdAt=u.created_at.strftime("%Y-%m-%d"),
    )
    for u in users
  ]


@router.post("/", response_model=UserSchema, status_code=201)
async def create_user(payload: UserCreate, _: UserSchema = Depends(get_current_admin), db: Session = Depends(get_db)):
  existing = db.query(UserModel).filter(UserModel.username == payload.username).first()
  if existing:
    raise HTTPException(status_code=400, detail="Username already exists")

  user = UserModel(
    id=str(int(datetime.utcnow().timestamp() * 1000)),
    username=payload.username,
    password_hash=hash_password(payload.password),
    role=payload.role,
    status="active",
  )
  db.add(user)
  db.commit()
  db.refresh(user)

  return UserSchema(
    id=user.id,
    username=user.username,
    role=user.role,  # type: ignore[arg-type]
    status=user.status,  # type: ignore[arg-type]
    createdAt=user.created_at.strftime("%Y-%m-%d"),
  )
