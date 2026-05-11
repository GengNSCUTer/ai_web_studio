from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.memory_repo import UserMemoryRepository
from app.schemas.memory import UserMemoryCreate, UserMemoryResponse, UserMemoryUpdate
from app.services.memory_service import MemoryService

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("", response_model=list[UserMemoryResponse])
def list_memories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UserMemoryResponse]:
    service = MemoryService(UserMemoryRepository(db))
    return service.list_memories(current_user.id)


@router.post("", response_model=UserMemoryResponse, status_code=status.HTTP_201_CREATED)
def create_memory(
    payload: UserMemoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserMemoryResponse:
    service = MemoryService(UserMemoryRepository(db))
    return service.create_memory(current_user.id, payload)


@router.patch("/{memory_id}", response_model=UserMemoryResponse)
def update_memory(
    memory_id: str,
    payload: UserMemoryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserMemoryResponse:
    repo = UserMemoryRepository(db)
    memory = repo.get_by_user(memory_id, current_user.id)
    if not memory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")

    service = MemoryService(repo)
    return service.update_memory(memory=memory, payload=payload)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(
    memory_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    repo = UserMemoryRepository(db)
    memory = repo.get_by_user(memory_id, current_user.id)
    if not memory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")

    repo.delete(memory)
