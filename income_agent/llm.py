from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from income_agent.config import AgentConfig, LLMConfig
from income_agent.storage import Store

log = logging.getLogger(__name__)

LLM_NOTE_PREFIX = "llm:"


class LLMError(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLM(Protocol):
    model: str

    def complete(self, system: str, user: str, *, json_mode: bool = False,
                 max_tokens: int = 1500) -> LLMResponse: ...


class OpenAICompatibleLLM:
    """Any endpoint that speaks the OpenAI /chat/completions protocol
    (OpenAI, Azure OpenAI, OpenRouter, Groq, Ollama, LM Studio, ...)."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, user: str, *, json_mode: bool = False,
                 max_tokens: int = 1500) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            raise LLMError(f"LLM HTTP {e.code}: {e.read()[:500]!r}") from e
        except urllib.error.URLError as e:
            raise LLMError(f"LLM connection error: {e.reason}") from e

        usage = data.get("usage") or {}
        return LLMResponse(
            text=data["choices"][0]["message"]["content"] or "",
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
        )


class MockLLM:
    """Offline stand-in so the whole pipeline can be exercised without an API key.
    Dispatches on the `TASK:` marker that every prompt in this package carries."""

    model = "mock"

    def complete(self, system: str, user: str, *, json_mode: bool = False,
                 max_tokens: int = 1500) -> LLMResponse:
        task = re.search(r"TASK:\s*(\w+)", system)
        name = task.group(1) if task else ""
        if name == "score_job":
            text = json.dumps({"score": 0.8, "reason": "[mock] プロフィールのスキルと一致しています。"},
                              ensure_ascii=False)
        elif name == "draft_proposal":
            text = ("[mock] はじめまして。ご依頼内容を拝見し、これまでの経験を活かして"
                    "お力になれると考えご連絡しました。\n\n"
                    "- 進め方: 要件確認 → 試作 → 修正 → 納品\n- 想定期間: 応相談\n\n"
                    "ご検討のほどよろしくお願いいたします。")
        elif name == "ideate_topics":
            text = json.dumps({"topics": [
                "[mock] 初心者向け: 自動化ツールの選び方ガイド",
                "[mock] 作業時間を半分にする5つの方法",
                "[mock] よくある失敗とその対策まとめ",
            ]}, ensure_ascii=False)
        elif name == "write_article":
            title = re.search(r"タイトル:\s*(.+)", user)
            heading = title.group(1).strip() if title else "記事"
            text = (f"# {heading}\n\n[mock] この記事では要点をわかりやすく解説します。\n\n"
                    "## ポイント1\n\n本文...\n\n## ポイント2\n\n本文...\n\n## まとめ\n\n本文...\n")
        else:
            text = "[mock] OK"
        return LLMResponse(text=text)


class BudgetedLLM:
    """Wraps an LLM, records every call's cost in the ledger, and refuses
    to make calls once today's spend reaches the configured daily budget."""

    def __init__(self, inner: LLM, store: Store, llm_cfg: LLMConfig, agent_cfg: AgentConfig):
        self.inner = inner
        self.store = store
        self.llm_cfg = llm_cfg
        self.agent_cfg = agent_cfg

    @property
    def model(self) -> str:
        return self.inner.model

    def spent_today_usd(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        spent = self.store.total("cost", since=today, note_prefix=LLM_NOTE_PREFIX)
        return spent / self.agent_cfg.usd_to_currency

    def remaining_usd(self) -> float:
        return self.agent_cfg.daily_llm_budget_usd - self.spent_today_usd()

    def complete(self, system: str, user: str, *, strategy: str, json_mode: bool = False,
                 max_tokens: int = 1500) -> LLMResponse:
        if self.remaining_usd() <= 0:
            raise BudgetExceeded(
                f"daily LLM budget ${self.agent_cfg.daily_llm_budget_usd:.2f} reached"
            )
        resp = self.inner.complete(system, user, json_mode=json_mode, max_tokens=max_tokens)
        cost_usd = (resp.input_tokens * self.llm_cfg.input_price_per_1m
                    + resp.output_tokens * self.llm_cfg.output_price_per_1m) / 1_000_000
        if cost_usd > 0:
            self.store.record(
                strategy, "cost", cost_usd * self.agent_cfg.usd_to_currency,
                f"{LLM_NOTE_PREFIX}{self.model} in={resp.input_tokens} out={resp.output_tokens}",
            )
        return resp

    def complete_json(self, system: str, user: str, *, strategy: str,
                      max_tokens: int = 800) -> dict[str, Any]:
        resp = self.complete(system, user, strategy=strategy, json_mode=True,
                             max_tokens=max_tokens)
        return parse_json(resp.text)


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise LLMError(f"LLM did not return JSON: {text[:200]!r}")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise LLMError(f"expected a JSON object, got {type(value).__name__}")
    return value


def build_llm(cfg: LLMConfig) -> LLM:
    provider = cfg.provider.lower()
    if provider == "mock":
        return MockLLM()
    api_key = os.environ.get(cfg.api_key_env, "")
    if provider == "auto" and not api_key:
        log.warning("%s is not set; falling back to the offline mock LLM", cfg.api_key_env)
        return MockLLM()
    if provider not in ("auto", "openai"):
        raise ValueError(f"unknown llm.provider: {cfg.provider!r}")
    if not api_key and "localhost" not in cfg.base_url and "127.0.0.1" not in cfg.base_url:
        raise ValueError(f"environment variable {cfg.api_key_env} is not set")
    return OpenAICompatibleLLM(cfg.base_url, api_key or "local", cfg.model, cfg.timeout_seconds)
