from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, DateTime, Text, Integer, ForeignKey

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


class AgentSession(Base):
  __tablename__ = "agent_sessions"

  id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
  started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
  ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
  model: Mapped[str] = mapped_column(String, nullable=False)
  channel: Mapped[str] = mapped_column(String, nullable=False)
  tokens_in: Mapped[int] = mapped_column(Integer, default=0)
  tokens_out: Mapped[int] = mapped_column(Integer, default=0)
  status: Mapped[str] = mapped_column(String, nullable=False, default="running")
  summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class TimelineEvent(Base):
  __tablename__ = "timeline_events"

  id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
  session_id: Mapped[str] = mapped_column(String, ForeignKey("agent_sessions.id"), index=True)
  type: Mapped[str] = mapped_column(String, nullable=False)
  timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
  content: Mapped[str] = mapped_column(Text, nullable=False)
  metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class ToolCall(Base):
  __tablename__ = "tool_calls"

  id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
  session_id: Mapped[str] = mapped_column(String, ForeignKey("agent_sessions.id"), index=True)
  name: Mapped[str] = mapped_column(String, nullable=False)
  input: Mapped[str] = mapped_column(Text, nullable=False)
  output: Mapped[str] = mapped_column(Text, nullable=False)
  started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
  ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
  status: Mapped[str] = mapped_column(String, nullable=False, default="success")


class Prompt(Base):
  __tablename__ = "prompts"

  id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
  session_id: Mapped[str] = mapped_column(String, ForeignKey("agent_sessions.id"), index=True)
  role: Mapped[str] = mapped_column(String, nullable=False)
  content: Mapped[str] = mapped_column(Text, nullable=False)
  timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
  tokens: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------------------
# Other Research
# ---------------------------------------------------------------------------

class OtherResearchConfig(Base):
  __tablename__ = "other_research_config"

  id: Mapped[str] = mapped_column(String, primary_key=True, default="default")
  topic: Mapped[str] = mapped_column(String, nullable=False, default="Battery Recycling")
  set_by: Mapped[str] = mapped_column(String, nullable=False, default="admin")
  updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
  enabled: Mapped[bool] = mapped_column(default=True)


class OtherResearchReport(Base):
  __tablename__ = "other_research_reports"

  id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
  title: Mapped[str] = mapped_column(String, nullable=False)
  source: Mapped[str] = mapped_column(String, nullable=False)
  date: Mapped[str] = mapped_column(String, nullable=False)
  score: Mapped[int] = mapped_column(Integer, default=0)
  link: Mapped[str] = mapped_column(String, nullable=False, default="#")
  topic: Mapped[str] = mapped_column(String, nullable=False, index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OtherIdeaDrop(Base):
  __tablename__ = "other_idea_drops"

  id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
  title: Mapped[str] = mapped_column(String, nullable=False)
  hook: Mapped[str] = mapped_column(String, nullable=False)
  category: Mapped[str] = mapped_column(String, nullable=False)
  priority: Mapped[str] = mapped_column(String, nullable=False, default="medium")
  tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
  created_by: Mapped[str] = mapped_column(String, nullable=False, default="admin")
  created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OtherNextAction(Base):
  __tablename__ = "other_next_actions"

  id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
  label: Mapped[str] = mapped_column(String, nullable=False)
  action_type: Mapped[str] = mapped_column(String, nullable=False, default="Research")
  completed: Mapped[bool] = mapped_column(default=False)
  created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# AI Research
# ---------------------------------------------------------------------------

class AIResearchConfig(Base):
  __tablename__ = "ai_research_config"

  id: Mapped[str] = mapped_column(String, primary_key=True, default="default")
  topic: Mapped[str] = mapped_column(String, nullable=False, default="Artificial Intelligence")
  set_by: Mapped[str] = mapped_column(String, nullable=False, default="admin")
  updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
  enabled: Mapped[bool] = mapped_column(default=True)


class AIResearchReport(Base):
  __tablename__ = "ai_research_reports"

  id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
  title: Mapped[str] = mapped_column(String, nullable=False)
  source: Mapped[str] = mapped_column(String, nullable=False)
  date: Mapped[str] = mapped_column(String, nullable=False)
  score: Mapped[int] = mapped_column(Integer, default=0)
  link: Mapped[str] = mapped_column(String, nullable=False, default="#")
  topic: Mapped[str] = mapped_column(String, nullable=False, index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AIIdeaDrop(Base):
  __tablename__ = "ai_idea_drops"

  id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
  title: Mapped[str] = mapped_column(String, nullable=False)
  hook: Mapped[str] = mapped_column(String, nullable=False)
  category: Mapped[str] = mapped_column(String, nullable=False)
  priority: Mapped[str] = mapped_column(String, nullable=False, default="medium")
  tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
  created_by: Mapped[str] = mapped_column(String, nullable=False, default="admin")
  created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AINextAction(Base):
  __tablename__ = "ai_next_actions"

  id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
  label: Mapped[str] = mapped_column(String, nullable=False)
  action_type: Mapped[str] = mapped_column(String, nullable=False, default="Research")
  completed: Mapped[bool] = mapped_column(default=False)
  created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
