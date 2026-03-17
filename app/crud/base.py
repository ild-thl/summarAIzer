"""Base CRUD operations interface."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from sqlalchemy.orm import Session

T = TypeVar("T")
CreateSchemaType = TypeVar("CreateSchemaType")
UpdateSchemaType = TypeVar("UpdateSchemaType")


class CRUDBase(ABC, Generic[T, CreateSchemaType, UpdateSchemaType]):
    """Base CRUD operations class."""

    def __init__(self, model: type[T]):
        self.model = model

    @abstractmethod
    def create(self, db: Session, obj_in: CreateSchemaType) -> T:
        """Create a new record."""
        pass

    @abstractmethod
    def read(self, db: Session, id: int) -> T | None:
        """Read a record by ID."""
        pass

    @abstractmethod
    def read_by_uri(self, db: Session, uri: str) -> T | None:
        """Read a record by URI."""
        pass

    @abstractmethod
    def list_all(self, db: Session, skip: int = 0, limit: int = 100) -> list[T]:
        """List all records with pagination."""
        pass

    @abstractmethod
    def update(self, db: Session, id: int, obj_in: UpdateSchemaType) -> T | None:
        """Update a record."""
        pass

    @abstractmethod
    def delete(self, db: Session, id: int) -> bool:
        """Delete a record."""
        pass
