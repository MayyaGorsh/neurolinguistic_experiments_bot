from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Trial:
    """одна проба внутри фазы"""
    trial_index: int = 0
    stimulus_content: str = ""          # текст стимула или имя файла
    stimulus_type: str = "text"         # text / audio / image / video
    stimulus_metadata: dict = field(default_factory=dict)
    response_options: list = field(default_factory=list)  # варианты кнопок
    correct_answer: Optional[str] = None
    auxiliary: dict = field(default_factory=dict)
    list_id: Optional[str] = None       # для распределения по листам

    def to_dict(self):
        return {
            "trial_index": self.trial_index,
            "stimulus_content": self.stimulus_content,
            "stimulus_type": self.stimulus_type,
            "stimulus_metadata": self.stimulus_metadata,
            "response_options": self.response_options,
            "correct_answer": self.correct_answer,
            "auxiliary": self.auxiliary,
            "list_id": self.list_id,
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**{k: data[k] for k in data if k in cls.__dataclass_fields__})


@dataclass
class Phase:
    """фаза эксперимента — блок проб одного типа"""
    phase_index: int = 0
    title: str = ""
    instruction: str = ""
    stimulus_type: str = "text"         # text / audio / image / video
    response_type: str = "buttons"      # buttons / likert / multiple_choice / open_text / voice
    trials: list = field(default_factory=list)  # список Trial.to_dict()
    randomize_order: bool = False
    time_limit: Optional[int] = None    # секунды на ответ
    settings: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "phase_index": self.phase_index,
            "title": self.title,
            "instruction": self.instruction,
            "stimulus_type": self.stimulus_type,
            "response_type": self.response_type,
            "trials": self.trials,
            "randomize_order": self.randomize_order,
            "time_limit": self.time_limit,
            "settings": self.settings,
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**{k: data[k] for k in data if k in cls.__dataclass_fields__})


@dataclass
class Experiment:
    """эксперимент, созданный исследователем"""
    owner_id: int = 0                   # telegram_id исследователя
    title: str = ""
    description: str = ""
    template_type: str = "free_form"    # тип шаблона или free_form
    status: str = "draft"               # draft / active / archived
    phases: list = field(default_factory=list)  # список Phase.to_dict()
    randomize_trials: bool = False
    use_lists: bool = False
    lists_count: int = 1
    time_limit: Optional[int] = None
    idle_timeout_seconds: int = 300     # 0 — отключено: сессия всегда возобновляется
    audio_silence_seconds: int = 0      # тишина в конце аудио (только для audio-шаблонов)
    collect_demographics: bool = False
    demographics_type: str = "standard" # standard / custom
    demographics_custom: list = field(default_factory=list)
    allow_repeat: bool = False
    export_settings: dict = field(default_factory=dict)
    deep_link_id: Optional[str] = None  # уникальный код для ссылки
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self):
        return {
            "owner_id": self.owner_id,
            "title": self.title,
            "description": self.description,
            "template_type": self.template_type,
            "status": self.status,
            "phases": self.phases,
            "randomize_trials": self.randomize_trials,
            "use_lists": self.use_lists,
            "lists_count": self.lists_count,
            "time_limit": self.time_limit,
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "audio_silence_seconds": self.audio_silence_seconds,
            "collect_demographics": self.collect_demographics,
            "demographics_type": self.demographics_type,
            "demographics_custom": self.demographics_custom,
            "allow_repeat": self.allow_repeat,
            "export_settings": self.export_settings,
            "deep_link_id": self.deep_link_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict):
        fields = cls.__dataclass_fields__
        return cls(**{k: data[k] for k in data if k in fields and k != "_id"})
