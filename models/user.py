from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class User:
    """базовый пользователь бота.
    имя/фамилию/username из Telegram-профиля намеренно не сохраняем:
    в боте они нигде не используются, а согласие респондента
    обещает анонимизированную обработку - идентификация только
    по telegram_id."""
    telegram_id: int
    role: str = "participant"  # "researcher" или "participant"
    created_at: datetime = field(default_factory=datetime.utcnow)
    # согласие респондента на обработку данных и рассылку приглашений.
    # запрашивается единожды при первом переходе по deep-link.
    consent_given: bool = False
    consent_at: Optional[datetime] = None

    def to_dict(self):
        return {
            "telegram_id": self.telegram_id,
            "role": self.role,
            "created_at": self.created_at,
            "consent_given": self.consent_given,
            "consent_at": self.consent_at,
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            telegram_id=data["telegram_id"],
            role=data.get("role", "participant"),
            created_at=data.get("created_at", datetime.utcnow()),
            consent_given=data.get("consent_given", False),
            consent_at=data.get("consent_at"),
        )
