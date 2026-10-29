from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from income_agent.feeds import Fetcher
from income_agent.llm import BudgetedLLM
from income_agent.storage import Action, Store


@dataclass
class Context:
    llm: BudgetedLLM
    store: Store
    data_dir: Path
    fetch: Fetcher
    currency: str
    timezone: str = "Asia/Tokyo"
    manual: bool = False  # started by hand (e.g. "Run workflow"), not by the schedule


class Strategy(ABC):
    """An income strategy. `run()` does the autonomous work and puts anything
    that touches money, accounts or the public internet into the approval queue."""

    name: str = ""
    description: str = ""

    def __init__(self, cfg: dict[str, Any], ctx: Context):
        self.cfg = cfg
        self.ctx = ctx
        self.log = logging.getLogger(f"income_agent.{self.name}")

    @abstractmethod
    def run(self) -> list[int]:
        """Performs one cycle and returns the ids of actions it created."""

    def on_approve(self, action: Action) -> str:
        """Executes an approved action and returns a message for the operator."""
        return "承認しました。"
