from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from income_agent.config import Config
from income_agent.feeds import Fetcher, http_fetch
from income_agent.llm import LLM, BudgetedLLM, BudgetExceeded, build_llm
from income_agent.storage import APPROVED, DONE, PENDING, REJECTED, Store
from income_agent.strategies import REGISTRY, Context, Strategy

log = logging.getLogger(__name__)


@dataclass
class CycleResult:
    created: dict[str, list[int]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    budget_exhausted: bool = False


class Agent:
    def __init__(self, config: Config, *, llm: LLM | None = None,
                 fetch: Fetcher = http_fetch, store: Store | None = None):
        self.config = config
        self.store = store or Store(config.agent.data_dir / "agent.db")
        self.llm = BudgetedLLM(llm or build_llm(config.llm), self.store, config.llm, config.agent)
        self.ctx = Context(llm=self.llm, store=self.store, data_dir=config.agent.data_dir,
                           fetch=fetch, currency=config.agent.currency,
                           timezone=config.agent.timezone)
        self.strategies: dict[str, Strategy] = {}
        for name, cfg in config.strategies.items():
            cls = REGISTRY.get(name)
            if cls is None:
                log.warning("unknown strategy %r in config; skipping", name)
                continue
            if cfg.get("enabled", True):
                self.strategies[name] = cls(cfg, self.ctx)

    def run_once(self) -> CycleResult:
        result = CycleResult()
        for name, strategy in self.strategies.items():
            log.info("running %s", name)
            try:
                result.created[name] = strategy.run()
            except BudgetExceeded as e:
                log.warning("%s: %s; stopping this cycle", name, e)
                result.budget_exhausted = True
                break
            except Exception as e:
                log.exception("%s failed", name)
                result.errors[name] = str(e)
        return result

    def run_forever(self, max_cycles: int | None = None) -> None:
        interval = max(1, self.config.agent.interval_minutes) * 60
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            result = self.run_once()
            total = sum(len(v) for v in result.created.values())
            log.info("cycle done: %d new item(s) in the approval queue, %d pending total",
                     total, len(self.store.list_actions(PENDING)))
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            time.sleep(interval)

    def approve(self, action_id: int) -> str:
        action = self._pending(action_id)
        strategy = self.strategies.get(action.strategy)
        if strategy is None:
            cls = REGISTRY[action.strategy]
            strategy = cls(self.config.strategies.get(action.strategy, {}), self.ctx)
        message = strategy.on_approve(action)
        self.store.set_status(action_id, DONE if action.kind == "article" else APPROVED)
        return message

    def reject(self, action_id: int) -> None:
        self._pending(action_id)
        self.store.set_status(action_id, REJECTED)

    def _pending(self, action_id: int):
        action = self.store.get_action(action_id)
        if action is None:
            raise KeyError(f"action #{action_id} not found")
        if action.status != PENDING:
            raise ValueError(f"action #{action_id} is already {action.status}")
        return action
