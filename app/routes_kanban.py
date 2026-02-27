from datetime import datetime
from uuid import uuid4
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .deps import get_db, get_current_user
from .models import KanbanCard as KanbanCardModel
from .schemas import KanbanCard as KanbanCardSchema, KanbanCardCreate, KanbanMoveRequest

router = APIRouter(prefix="/kanban", tags=["kanban"])


@router.get("/cards", response_model=List[KanbanCardSchema])
async def list_cards(
  db: Session = Depends(get_db),
  _user = Depends(get_current_user),
):
  cards = db.query(KanbanCardModel).all()
  return [
    KanbanCardSchema(
      id=c.id,
      title=c.title,
      description=c.description,
      column=c.column,  # type: ignore[arg-type]
      priority=c.priority,  # type: ignore[arg-type]
      createdAt=c.created_at.strftime("%Y-%m-%d"),
    )
    for c in cards
  ]


@router.post("/cards", response_model=KanbanCardSchema, status_code=201)
async def create_card(
  payload: KanbanCardCreate,
  db: Session = Depends(get_db),
  _user = Depends(get_current_user),
):
  card = KanbanCardModel(
    id=str(uuid4()),
    title=payload.title,
    description=payload.description,
    column=payload.column,
    priority=payload.priority,
  )
  db.add(card)
  db.commit()
  db.refresh(card)

  return KanbanCardSchema(
    id=card.id,
    title=card.title,
    description=card.description,
    column=card.column,  # type: ignore[arg-type]
    priority=card.priority,  # type: ignore[arg-type]
    createdAt=card.created_at.strftime("%Y-%m-%d"),
  )


@router.post("/cards/{card_id}/move", response_model=KanbanCardSchema)
async def move_card(
  card_id: str,
  payload: KanbanMoveRequest,
  db: Session = Depends(get_db),
  _user = Depends(get_current_user),
):
  card = db.query(KanbanCardModel).filter(KanbanCardModel.id == card_id).first()
  if not card:
    raise HTTPException(status_code=404, detail="Card not found")

  card.column = payload.column
  db.commit()
  db.refresh(card)

  return KanbanCardSchema(
    id=card.id,
    title=card.title,
    description=card.description,
    column=card.column,  # type: ignore[arg-type]
    priority=card.priority,  # type: ignore[arg-type]
    createdAt=card.created_at.strftime("%Y-%m-%d"),
  )


@router.delete("/cards/{card_id}", status_code=204)
async def delete_card(
  card_id: str,
  db: Session = Depends(get_db),
  _user = Depends(get_current_user),
):
  card = db.query(KanbanCardModel).filter(KanbanCardModel.id == card_id).first()
  if not card:
    raise HTTPException(status_code=404, detail="Card not found")

  db.delete(card)
  db.commit()
  return None
