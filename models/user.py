from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class User:
    """базовый пользователь бота"""
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: str = "participant"  # "researcher" или "participant"
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self):
        return {
            "telegram_id": self.telegram_id,
            "username": self.username,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "role": self.role,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            telegram_id=data["telegram_id"],
            username=data.get("username"),
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            role=data.get("role", "participant"),
            created_at=data.get("created_at", datetime.utcnow()),
        )
