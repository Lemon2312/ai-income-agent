from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AgentConfig:
    data_dir: Path
    interval_minutes: int = 60
    timezone: str = "Asia/Tokyo"
    currency: str = "JPY"
    usd_to_currency: float = 150.0
    daily_llm_budget_usd: float = 1.0


@dataclass
class LLMConfig:
    provider: str = "auto"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"
    input_price_per_1m: float = 0.15
    output_price_per_1m: float = 0.60
    timeout_seconds: int = 120


@dataclass
class Config:
    path: Path
    agent: AgentConfig
    llm: LLMConfig
    strategies: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def base_dir(self) -> Path:
        return self.path.parent


def _resolve(base: Path, value: str | Path) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    with path.open("rb") as f:
        raw = tomllib.load(f)

    agent_raw = dict(raw.get("agent", {}))
    agent_raw["data_dir"] = _resolve(path.parent, agent_raw.get("data_dir", "data"))
    agent = AgentConfig(**agent_raw)
    llm = LLMConfig(**raw.get("llm", {}))

    strategies = raw.get("strategies", {})
    for cfg in strategies.values():
        for key in ("publish_dir", "site_dir"):
            if key in cfg:
                cfg[key] = str(_resolve(path.parent, cfg[key]))

    return Config(path=path, agent=agent, llm=llm, strategies=strategies)
