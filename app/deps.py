from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .database import SessionLocal, engine, Base
from .models import User as UserModel
from .schemas import User as UserSchema

# Security settings (for now, static; later can move to env)
SECRET_KEY = "CHANGE_ME_TO_SOMETHING_RANDOM_AND_SECURE"  # TODO: load from env
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# Use pbkdf2_sha256 to avoid bcrypt backend issues on this environment
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# Ensure tables exist
Base.metadata.create_all(bind=engine)

# Seed a default admin user if none exists
with SessionLocal() as _db:
  existing_admin = (
    _db.query(UserModel)
    .filter(UserModel.username == "admin")
    .first()
  )
  if existing_admin is None:
    from uuid import uuid4

    admin_user = UserModel(
      id=str(uuid4()),
      username="admin",
      password_hash=pwd_context.hash("admin"),
      role="admin",
      status="active",
    )
    _db.add(admin_user)
    _db.commit()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_db():
  db = SessionLocal()
  try:
    yield db
  finally:
    db.close()


def verify_password(plain_password: str, password_hash: str) -> bool:
  return pwd_context.verify(plain_password, password_hash)


def hash_password(password: str) -> str:
  return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
  to_encode = data.copy()
  expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
  to_encode.update({"exp": expire})
  encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
  return encoded_jwt


def get_user_by_username(db: Session, username: str) -> Optional[UserModel]:
  return db.query(UserModel).filter(UserModel.username == username).first()


async def get_current_user(
  token: str = Depends(oauth2_scheme),
  db: Session = Depends(get_db),
) -> UserSchema:
  credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
  )
  try:
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    username: str | None = payload.get("sub")
    if username is None:
      raise credentials_exception
  except JWTError:
    raise credentials_exception

  user = get_user_by_username(db, username=username)
  if user is None:
    raise credentials_exception

  return UserSchema(
    id=user.id,
    username=user.username,
    role=user.role,  # type: ignore[arg-type]
    status=user.status,  # type: ignore[arg-type]
    createdAt=user.created_at.strftime("%Y-%m-%d"),
  )


async def get_current_admin(user: UserSchema = Depends(get_current_user)) -> UserSchema:
  if user.role != "admin":
    raise HTTPException(status_code=403, detail="Admin privileges required")
  return user
