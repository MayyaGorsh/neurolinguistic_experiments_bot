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
    # согласие респондента на обработку данных и рассылку приглашений.
    # запрашивается единожды при первом переходе по deep-link.
    consent_given: bool = False
    consent_at: Optional[datetime] = None

    def to_dict(self):
        return {
            "telegram_id": self.telegram_id,
            "username": self.username,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "role": self.role,
            "created_at": self.created_at,
            "consent_given": self.consent_given,
            "consent_at": self.consent_at,
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
            consent_given=data.get("consent_given", False),
            consent_at=data.get("consent_at"),
        )
