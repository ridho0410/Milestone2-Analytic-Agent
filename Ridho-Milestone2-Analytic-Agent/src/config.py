"""Konfigurasi runtime; kredensial tidak pernah ditanamkan dalam kode sumber."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = BASE_DIR / ".env") -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name.strip(), value)


def _number(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(value, maximum))


def _path(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    return value if value.is_absolute() else BASE_DIR / value


@dataclass(frozen=True)
class Settings:
    gold_backend: str
    gold_database_url: str
    gold_row_limit: int
    gold_result_byte_limit: int
    gold_timeout_seconds: float
    gold_connect_timeout_seconds: int
    data_dir: Path
    sqlite_path: Path
    demo_token_a: str
    demo_token_b: str
    history_limit: int
    max_message_chars: int
    max_title_chars: int
    max_note_chars: int
    dataset_start: str
    dataset_end: str
    llm_enabled: bool
    openai_api_key: str
    openai_base_url: str
    llm_model: str
    llm_reasoning_effort: str
    llm_api_mode: str
    llm_timeout_seconds: float
    llm_max_output_tokens: int
    max_tool_calls: int
    max_model_calls: int
    max_query_calls: int
    max_repair_attempts: int
    agent_timeout_seconds: float
    expose_sql: bool


def load_settings() -> Settings:
    data_dir = _path("GOLD_DATA_DIR", "data")
    # Pertahankan kompatibilitas scaffold Compose dengan mode CSV lokal.
    mounted_data = BASE_DIR / "gold-data"
    if (
        os.getenv("GOLD_DATA_DIR", "data") in {"data", "./data"}
        and (mounted_data / "manifest.json").is_file()
    ):
        data_dir = mounted_data
    return Settings(
        gold_backend=os.getenv("GOLD_BACKEND", "sqlite").lower(),
        gold_database_url=os.getenv("GOLD_DATABASE_URL", ""),
        gold_row_limit=int(_number("GOLD_ROW_LIMIT", 500, 1, 5000)),
        gold_result_byte_limit=int(
            _number("GOLD_RESULT_BYTE_LIMIT", 200000, 1024, 1000000)
        ),
        gold_timeout_seconds=_number("GOLD_TIMEOUT_SECONDS", 8, 0.01, 120),
        gold_connect_timeout_seconds=int(
            _number("GOLD_CONNECT_TIMEOUT_SECONDS", 8, 1, 60)
        ),
        data_dir=data_dir,
        sqlite_path=_path("SQLITE_PATH", "data/conversations.sqlite3"),
        demo_token_a=os.getenv("DEMO_TOKEN_A", ""),
        demo_token_b=os.getenv("DEMO_TOKEN_B", ""),
        history_limit=int(_number("HISTORY_LIMIT", 12, 1, 50)),
        max_message_chars=int(_number("MAX_MESSAGE_CHARS", 2000, 1, 10000)),
        max_title_chars=int(_number("MAX_TITLE_CHARS", 80, 1, 200)),
        max_note_chars=int(_number("MAX_NOTE_CHARS", 400, 1, 1000)),
        dataset_start=os.getenv("DATASET_START", "2026-07-01"),
        dataset_end=os.getenv("DATASET_END", "2026-09-20"),
        llm_enabled=os.getenv("LLM_ENABLED", "false").lower() in {"true", "1", "yes"},
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        llm_reasoning_effort=os.getenv("LLM_REASONING_EFFORT", ""),
        llm_api_mode=os.getenv("LLM_API_MODE", "auto"),
        llm_timeout_seconds=_number("LLM_TIMEOUT_SECONDS", 25, 0.1, 120),
        llm_max_output_tokens=int(_number("LLM_MAX_OUTPUT_TOKENS", 4000, 100, 16000)),
        max_tool_calls=int(_number("MAX_TOOL_CALLS", 3, 1, 10)),
        max_model_calls=int(_number("MAX_MODEL_CALLS", 6, 1, 20)),
        max_query_calls=int(_number("MAX_QUERY_CALLS", 3, 1, 10)),
        max_repair_attempts=int(_number("MAX_REPAIR_ATTEMPTS", 1, 0, 1)),
        agent_timeout_seconds=_number("AGENT_TIMEOUT_SECONDS", 60, 0.1, 180),
        expose_sql=os.getenv("EXPOSE_SQL", "false").lower() in {"true", "1"},
    )


load_dotenv()
settings = load_settings()
