from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, DateTime, Text

from .database import Base


class User(Base):
  __tablename__ = "users"

  id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
  username: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
  password_hash: Mapped[str] = mapped_column(String, nullable=False)
  role: Mapped[str] = mapped_column(String, nullable=False, default="operator")
  status: Mapped[str] = mapped_column(String, nullable=False, default="active")
  created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class KanbanCard(Base):
  __tablename__ = "kanban_cards"

  id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
  title: Mapped[str] = mapped_column(String, nullable=False)
  description: Mapped[str | None] = mapped_column(Text, nullable=True)
  column: Mapped[str] = mapped_column(String, nullable=False, default="parking")
  priority: Mapped[str] = mapped_column(String, nullable=False, default="medium")
  created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
