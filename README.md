# ai-income-agent

稼ぐための「作業」をAIが自動でこなし、お金やアカウントに関わる最終判断だけを人間が承認する、半自律型のAIエージェントです。

> **前提**: 完全放置で確実に稼げるAIは存在しません。無断の自動投稿・自動応募・自動売買は、プラットフォーム規約違反やアカウント停止、損失の原因になります。
> このエージェントは「探す・選ぶ・書く・記録する」を自動化し、人間の作業を承認ボタンを押すだけに近づけることを目指しています。

## できること

| 戦略 | 自動でやること | 人間がやること |
| --- | --- | --- |
| `freelance_scout`（案件スカウト） | RSS/Atomの求人・案件フィードを巡回し、キーワードで絞り込み、LLMでプロフィールとの適合度を採点して、提案文を下書きする | 下書きを確認し、自分のアカウントで応募する |
| `content_publisher`（ブログ×アフィリエイト） | ジャンルに沿って記事を企画・執筆し、アフィリエイトリンクを挿入してPR表記（ステマ規制対応）を付け、下書きを保存する | 下書きを確認して承認すると、公開用フォルダにコピーされる（`require_approval = false` にすると自動公開） |

共通の仕組み:

- **承認キュー**: 成果物はすべてキューに入り、`approve` / `reject` で処理します。
- **予算ガード**: LLM料金を毎回計算して記録し、1日の上限（`daily_llm_budget_usd`）に達したらその日は停止します。
- **収支台帳**: LLM費用は自動で、収益や他の費用は手動で記録し、戦略ごとのROIを `report` で確認できます。
- **重複排除**: 一度評価した案件や書いた記事タイトルは再処理しません。
- **依存ゼロ**: Python 3.11以上の標準ライブラリだけで動きます。LLMはOpenAI互換APIなら何でも使えます（OpenAI / OpenRouter / Groq / Ollama / LM Studio など）。

## セットアップ

```bash
pip install -e .            # income-agent コマンドが使えるようになります
income-agent init           # config.toml を作成
export OPENAI_API_KEY=sk-...  # 未設定ならオフラインのモックLLMで動作確認できます
```

`config.toml` で最低限、次を自分用に編集してください。

- `strategies.freelance_scout.profile`: スキル・実績・希望単価（提案文の品質に直結します）
- `strategies.freelance_scout.feeds` / `keywords`: 巡回するフィードと絞り込み語
- `strategies.content_publisher.niche` / `audience`: ブログのジャンルと読者
- `strategies.content_publisher.affiliate_links`: 自分のアフィリエイトリンク（キーワードが本文に最初に出た箇所がリンクになります）

## 使い方

```bash
income-agent run --once      # 1サイクル実行
income-agent run             # 常駐（interval_minutes ごとに実行）
income-agent queue           # 承認待ち一覧
income-agent show 3          # 詳細（提案文や記事本文）を表示
income-agent approve 3 5     # 承認（記事は公開フォルダへ、案件は応募URLを表示）
income-agent reject 4
income-agent revenue add 30000 --strategy freelance_scout --note "A社 LP改修"
income-agent cost add 1500 --note "ドメイン代"
income-agent report          # 収支・ROI
```

常駐させる場合は、`tmux` / `systemd` / cron（`run --once` を定期実行）などを使ってください。

## 公開までの流れ（ブログ）

`publish_dir`（デフォルト `site/content/posts`）は Hugo の記事フォルダ構成に合わせてあります。Hugo / Jekyll / Astro などのサイトをこのフォルダに向け、GitHub Pages や Cloudflare Pages に自動デプロイすれば、承認するだけで記事が公開されます。

## 戦略を追加する

`income_agent/strategies/base.py` の `Strategy` を継承して `run()`（自動でやる作業）と `on_approve()`（承認後の処理）を実装し、`strategies/__init__.py` の `REGISTRY` に登録して、設定に `[strategies.<name>]` を追加します。LLM呼び出しは `self.ctx.llm.complete(..., strategy=self.name)` を使うと、費用の記録と予算チェックが自動で行われます。

## 注意事項

- 求人・案件サイトの利用規約を守ってください。スクレイピングが禁止されているサイトは対象にしないでください（RSSなど公式に提供されているフィードの利用を推奨します）。
- アフィリエイト記事には広告であることの明示が必要です（景品表示法のステマ規制、2023年10月施行）。リンクが入った記事には自動でPR表記を付けています。
- AIが書いた記事は事実誤認を含むことがあります。承認前に必ず内容を確認してください。
- 収益の保証はありません。

## 開発

```bash
pip install -e '.[dev]'
pytest
```
