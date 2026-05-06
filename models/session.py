from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Answer:
    """один ответ респондента на пробу"""
    session_id: str = ""
    experiment_id: str = ""
    phase_index: int = 0
    trial_index: int = 0
    stimulus_id: str = ""
    raw_response: str = ""
    normalized_response: str = ""
    is_correct: Optional[bool] = None
    reaction_time_ms: Optional[int] = None  # мс от показа стимула до нажатия
    timed_out: bool = False
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "experiment_id": self.experiment_id,
            "phase_index": self.phase_index,
            "trial_index": self.trial_index,
            "stimulus_id": self.stimulus_id,
            "raw_response": self.raw_response,
            "normalized_response": self.normalized_response,
            "is_correct": self.is_correct,
            "reaction_time_ms": self.reaction_time_ms,
            "timed_out": self.timed_out,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict):
        fields = cls.__dataclass_fields__
        return cls(**{k: data[k] for k in data if k in fields})


@dataclass
class ParticipantSession:
    """сессия прохождения эксперимента респондентом"""
    telegram_id: int = 0
    experiment_id: str = ""
    status: str = "started"             # started / in_progress / completed / abandoned
    assigned_list: Optional[str] = None
    current_phase: int = 0
    current_trial: int = 0
    is_preview: bool = False
    demographics: dict = field(default_factory=dict)
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None  # для idle-таймаута

    def to_dict(self):
        return {
            "telegram_id": self.telegram_id,
            "experiment_id": self.experiment_id,
            "status": self.status,
            "assigned_list": self.assigned_list,
            "current_phase": self.current_phase,
            "current_trial": self.current_trial,
            "is_preview": self.is_preview,
            "demographics": self.demographics,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "last_activity_at": self.last_activity_at,
        }

    @classmethod
    def from_dict(cls, data: dict):
        fields = cls.__dataclass_fields__
        return cls(**{k: data[k] for k in data if k in fields})
