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
    # премиум-статус исследователя: снимает лимит на число экспериментов и
    # открывает доступ к рассылке. хранится как дата окончания подписки;
    # выставляется вручную в БД после проверки перевода (см.
    # handlers/premium.py и scripts/grant_premium.py).
    # None или дата в прошлом = премиум неактивен.
    premium_until: Optional[datetime] = None

    def to_dict(self):
        return {
            "telegram_id": self.telegram_id,
            "role": self.role,
            "created_at": self.created_at,
            "consent_given": self.consent_given,
            "consent_at": self.consent_at,
            "premium_until": self.premium_until,
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            telegram_id=data["telegram_id"],
            role=data.get("role", "participant"),
            created_at=data.get("created_at", datetime.utcnow()),
            consent_given=data.get("consent_given", False),
            consent_at=data.get("consent_at"),
            premium_until=data.get("premium_until"),
        )


def is_premium_active(user: Optional[dict]) -> bool:
    """активен ли премиум у пользователя.

    премиум считается активным, если у пользователя задано поле
    premium_until и оно строго больше текущего момента. отсутствие поля
    или дата в прошлом эквивалентны отсутствию премиума - доступ ко
    всем фримиум-гейтам закрыт автоматически после истечения срока,
    без какого-либо фонового задания.
    """
    if not user:
        return False
    until = user.get("premium_until")
    if not until:
        return False
    return until > datetime.utcnow()
