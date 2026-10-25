from __future__ import annotations

import argparse
import logging
import shutil
import sys
from importlib import resources
from pathlib import Path

from income_agent.agent import Agent
from income_agent.config import load_config
from income_agent.site import init_site
from income_agent.storage import PENDING

DEFAULT_CONFIG = "config.toml"


def _agent(args: argparse.Namespace) -> Agent:
    path = Path(args.config)
    if not path.exists():
        sys.exit(f"{path} が見つかりません。先に `income-agent init` を実行してください。")
    try:
        return Agent(load_config(path))
    except ValueError as e:
        sys.exit(f"設定エラー: {e}")


def cmd_init(args: argparse.Namespace) -> int:
    dest = Path(args.config)
    if dest.exists() and not args.force:
        print(f"{dest} は既に存在します（上書きするには --force）")
        return 1
    template = resources.files("income_agent").joinpath("config.example.toml")
    with resources.as_file(template) as src:
        shutil.copy(src, dest)
    print(f"{dest} を作成しました。プロフィール・ジャンル・フィードを編集してから `income-agent run --once` を実行してください。")
    return 0


def cmd_init_site(args: argparse.Namespace) -> int:
    content_cfg: dict = {}
    if Path(args.config).exists():
        content_cfg = load_config(args.config).strategies.get("content_publisher", {})
    dest = Path(args.dir or content_cfg.get("site_dir", "site"))
    title = args.title or content_cfg.get("site_title", "AIブログ")
    description = args.description or content_cfg.get("site_description", content_cfg.get("niche", ""))
    author = args.author or content_cfg.get("author", "運営者")
    try:
        steps = init_site(dest, title, description, author, git=not args.no_git)
    except FileExistsError as e:
        print(e)
        return 1
    print(f"ブログを作成しました: {dest}\n\n次の手順で GitHub Pages に公開できます:")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    agent = _agent(args)
    print(f"LLM: {agent.llm.model} / 本日の残り予算: ${agent.llm.remaining_usd():.3f}")
    if args.once:
        result = agent.run_once()
        for name, ids in result.created.items():
            pending = sum(1 for i in ids if agent.store.get_action(i).status == PENDING)
            print(f"  {name}: {len(ids)} 件を作成（公開・PR作成 {len(ids) - pending} 件 / 承認キュー {pending} 件）")
        for name, err in result.errors.items():
            print(f"  {name}: エラー {err}")
        if result.budget_exhausted:
            print("  本日のLLM予算に達したため途中で停止しました。")
        print(f"承認待ち: {len(agent.store.list_actions(PENDING))} 件（`income-agent queue` で確認）")
        return 1 if result.errors else 0
    agent.run_forever()
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    agent = _agent(args)
    actions = agent.store.list_actions(None if args.all else PENDING, limit=args.limit)
    if not actions:
        print("承認待ちはありません。")
        return 0
    for a in actions:
        extra = ""
        if "score" in a.payload:
            extra = f" score={a.payload['score']:.2f}"
        elif "quality" in a.payload:
            extra = f" quality={a.payload['quality']:.2f}"
        print(f"#{a.id:<4} [{a.status:<8}] {a.strategy:<18} {a.kind:<9}{extra}  {a.title}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    agent = _agent(args)
    a = agent.store.get_action(args.id)
    if a is None:
        print(f"#{args.id} は存在しません")
        return 1
    print(f"#{a.id} {a.title}\n戦略: {a.strategy} / 種類: {a.kind} / 状態: {a.status} / 作成: {a.created_at}\n")
    if a.kind == "proposal":
        print(f"URL: {a.payload.get('url')}\nスコア: {a.payload.get('score')}  理由: {a.payload.get('reason')}\n")
        print("----- 案件概要 -----\n" + a.payload.get("summary", ""))
        print("\n----- 提案文（下書き） -----\n" + a.payload.get("proposal", ""))
    elif a.kind in ("article", "article_rejected"):
        path = Path(a.payload["draft_path"])
        history = " → ".join(f"{s:.2f}" for s in a.payload.get("quality_history", []))
        print(f"下書き: {path}\n品質スコア: {history}")
        for issue in a.payload.get("issues", []):
            print(f"  - {issue}")
        print()
        if path.exists():
            print(path.read_text(encoding="utf-8"))
    else:
        print(a.payload)
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    agent = _agent(args)
    for action_id in args.ids:
        try:
            print(f"#{action_id}: {agent.approve(action_id)}")
        except (KeyError, ValueError, FileNotFoundError) as e:
            print(f"#{action_id}: {e}")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    agent = _agent(args)
    for action_id in args.ids:
        try:
            agent.reject(action_id)
            print(f"#{action_id}: 却下しました")
        except (KeyError, ValueError) as e:
            print(f"#{action_id}: {e}")
    return 0


def cmd_revenue(args: argparse.Namespace) -> int:
    agent = _agent(args)
    agent.store.record(args.strategy, "revenue", args.amount, args.note)
    print(f"収益 {args.amount:,.0f} {agent.config.agent.currency} を記録しました（{args.strategy}）")
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    agent = _agent(args)
    agent.store.record(args.strategy, "cost", args.amount, args.note)
    print(f"費用 {args.amount:,.0f} {agent.config.agent.currency} を記録しました（{args.strategy}）")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    agent = _agent(args)
    cur = agent.config.agent.currency
    rows = agent.store.summary_by_strategy()
    print(f"{'戦略':<20}{'収益':>12}{'費用':>12}{'利益':>12}")
    total_rev = total_cost = 0.0
    for name, v in sorted(rows.items()):
        total_rev += v["revenue"]
        total_cost += v["cost"]
        print(f"{name:<20}{v['revenue']:>12,.0f}{v['cost']:>12,.1f}{v['revenue'] - v['cost']:>12,.0f}")
    print(f"{'合計':<20}{total_rev:>12,.0f}{total_cost:>12,.1f}{total_rev - total_cost:>12,.0f}  ({cur})")
    if total_cost > 0:
        print(f"ROI: {(total_rev - total_cost) / total_cost * 100:,.0f}%")
    print(f"承認待ち: {len(agent.store.list_actions(PENDING))} 件 / 本日のLLM利用: ${agent.llm.spent_today_usd():.3f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="income-agent",
                                description="稼ぐための作業を自動化する半自律AIエージェント")
    p.add_argument("-c", "--config", default=DEFAULT_CONFIG, help="設定ファイル (default: config.toml)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="設定ファイルのひな形を作成")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("init-site", help="GitHub Pages 用のブログ（Hugo）を作成")
    s.add_argument("--dir", help="作成先（default: 設定の site_dir）")
    s.add_argument("--title")
    s.add_argument("--description")
    s.add_argument("--author")
    s.add_argument("--no-git", action="store_true", help="git init しない")
    s.set_defaults(func=cmd_init_site)

    s = sub.add_parser("run", help="エージェントを実行（デフォルトは常駐ループ）")
    s.add_argument("--once", action="store_true", help="1サイクルだけ実行して終了")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("queue", help="承認キューを表示")
    s.add_argument("--all", action="store_true", help="処理済みも表示")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_queue)

    s = sub.add_parser("show", help="アクションの詳細を表示")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("approve", help="承認して実行")
    s.add_argument("ids", type=int, nargs="+")
    s.set_defaults(func=cmd_approve)

    s = sub.add_parser("reject", help="却下")
    s.add_argument("ids", type=int, nargs="+")
    s.set_defaults(func=cmd_reject)

    for name, func, label in (("revenue", cmd_revenue, "収益"), ("cost", cmd_cost, "費用")):
        s = sub.add_parser(name, help=f"{label}を手動で記録")
        s_sub = s.add_subparsers(dest="op", required=True)
        add = s_sub.add_parser("add", help=f"{label}を追加")
        add.add_argument("amount", type=float)
        add.add_argument("--strategy", default="manual")
        add.add_argument("--note", default="")
        add.set_defaults(func=func)

    s = sub.add_parser("report", help="収支レポート")
    s.set_defaults(func=cmd_report)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)
