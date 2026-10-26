# ai-income-agent

稼ぐための「作業」をAIが自動でこなし、お金やアカウントに関わる最終判断だけを人間が承認する、半自律型のAIエージェントです。

> **前提**: 完全放置で確実に稼げるAIは存在しません。無断の自動投稿・自動応募・自動売買は、プラットフォーム規約違反やアカウント停止、損失の原因になります。
> このエージェントは「探す・選ぶ・書く・記録する」を自動化し、人間の作業を承認ボタンを押すだけに近づけることを目指しています。

## できること

| 戦略 | 自動でやること | 人間がやること |
| --- | --- | --- |
| `freelance_scout`（案件スカウト） | RSS/Atomの求人・案件フィードを巡回し、キーワードで絞り込み、LLMでプロフィールとの適合度を採点して、提案文を下書きする | 下書きを確認し、自分のアカウントで応募する |
| `content_publisher`（ブログ×アフィリエイト） | 記事の企画・執筆 → AI編集長による採点と書き直し → アフィリエイトリンク挿入とPR表記 → ブログへ追加 → git push で GitHub Pages に自動公開 | 最初にブログを用意するだけ。以後は放置で毎日記事が増える（`require_approval = true` で公開前チェックも可） |

共通の仕組み:

- **承認キュー**: 人間の確認が必要な成果物はキューに入り、`approve` / `reject` で処理します。
- **予算ガード**: LLM料金を毎回計算して記録し、1日の上限（`daily_llm_budget_usd`）に達したらその日は停止します。
- **収支台帳**: LLM費用は自動で、収益や他の費用は手動で記録し、戦略ごとのROIを `report` で確認できます。
- **重複排除**: 一度評価した案件や書いた記事タイトルは再処理しません。
- **依存ゼロ**: Python 3.11以上の標準ライブラリだけで動きます。LLMはOpenAI互換APIなら何でも使えます（OpenAI / OpenRouter / Groq / Ollama / LM Studio など）。

## GitHub だけで始める（PC・サーバー不要）

このリポジトリには、ブログ本体（`site/`）と、GitHub Actions で毎日9時台・18時台（日本時間）に記事を書く仕組みが入っています。設定はブラウザだけで完了します。

1. **APIキーを登録**: リポジトリの Settings → Secrets and variables → Actions → New repository secret で、名前 `OPENAI_API_KEY`、値に OpenAI のAPIキーを登録
2. **Pages を有効化**: Settings → Pages → Build and deployment の Source を「GitHub Actions」に変更
3. **プルリクエスト作成を許可**: Settings → Actions → General → Workflow permissions で「Allow GitHub Actions to create and approve pull requests」にチェックして Save
4. **試しに1回動かす**: Actions タブ → 「Blog agent」→「Run workflow」

数分後、記事がプルリクエストとして届きます（GitHub アプリならスマホに通知が来ます）。

- **公開する**: プルリクエストを「Merge」すると、数分でブログ（`https://<ユーザー名>.github.io/<リポジトリ名>/`）に公開されます
- **公開しない**: 「Close」するだけ。同じテーマは二度と書きません
- **直してから公開する**: 「Files changed」タブで記事を編集してからマージ

品質に納得したら、`agent.toml` の `deploy = "github-pr"` を `deploy = "git"` に変えると、確認なしの完全自動公開になります。ジャンル、1日の本数、品質基準、アフィリエイトリンクなども `agent.toml` で変更できます（GitHub 上で直接編集できます）。

実行に失敗したとき（APIキー未設定、予算超過以外のエラーなど）は GitHub からメールが届きます。実行履歴と収支のデータは `.agent/` に自動で保存されます。

## 手元で使う場合のセットアップ

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

## ブログの完全自動運用

ジャンルは `niche` / `audience` で決まります（このリポジトリの GitHub 運用では `agent.toml` の「理系大学生のためのAI勉強術」、手元用のひな形 `config.toml` では「フリーランスのためのAI時短術」）。`guidelines` にジャンル固有の執筆ルールを書くと、企画・執筆・採点のすべてでAIに渡され、違反した記事は0点になります。

### 1. ブログを作る（最初の1回だけ）

```bash
income-agent init-site       # site/ に Hugo ブログを作成し git init
```

表示される手順に従って GitHub に push し、リポジトリの Settings → Pages → Source を「GitHub Actions」にすると、`https://<ユーザー名>.github.io/<リポジトリ名>/` で公開されます。ブログには次のものが含まれています。

- 記事一覧・記事ページ・タグページ・関連記事・ページ送り
- `sitemap.xml`、`robots.txt`、RSS、検索結果用の説明文（description）、OGP
- 運営者情報とプライバシーポリシーのページ（アフィリエイトの審査で求められることが多いもの）
- アフィリエイトリンクへの `rel="sponsored nofollow"` の自動付与（Google が求める有料リンクの明示）
- 記事末尾の案内枠（`hugo.toml` の `ctaURL` などを設定すると表示。将来のデジタル商品への導線用）

### 2. 自動で回す

```bash
export OPENAI_API_KEY=sk-...
income-agent run             # 1時間ごとに実行。1日の上限に達するまで記事を公開
```

1記事ごとの流れ:

1. タイトルを決める（`topics` に書いたものを優先し、なくなったらAIが既存記事と被らないテーマを企画）
2. 見出し構成（導入・見出し4〜6個・まとめ）を作り、見出しごとに分けて執筆する（小さなモデルでも文字数を確保するため）
3. AI編集長が実用性・正確性・深さ・読みやすさ・表現の適切さを各5点満点で採点し、平均が `min_quality`（初期値0.8 = 平均4点）未満なら、指摘を渡して見出しごとに書き直す（最大 `max_revisions` 回。短くなった書き直しは採用しない）。文字数が目安の7割未満なら不合格、誇大表現などがあれば0点
4. それでも基準未満なら**公開しない**（`income-agent queue --all` で `article_rejected` として確認可能）。同じテーマへの再挑戦は `max_attempts_per_topic` 回まで
5. 合格したらアフィリエイトリンクとPR表記を付けて `site/content/posts/` に追加し、commit & push。GitHub Actions がビルドし、数分で公開されます

量産スパムと判定されないよう、公開数は `max_articles_per_day`（初期値2）までに制限しています。日付の区切りは `agent.timezone`（初期値 Asia/Tokyo）です。push に失敗しても記事はローカルにコミットされ、次回の push でまとめて公開されます。

## 戦略を追加する

`income_agent/strategies/base.py` の `Strategy` を継承して `run()`（自動でやる作業）と `on_approve()`（承認後の処理）を実装し、`strategies/__init__.py` の `REGISTRY` に登録して、設定に `[strategies.<name>]` を追加します。LLM呼び出しは `self.ctx.llm.complete(..., strategy=self.name)` を使うと、費用の記録と予算チェックが自動で行われます。

## 注意事項

- 求人・案件サイトの利用規約を守ってください。スクレイピングが禁止されているサイトは対象にしないでください（RSSなど公式に提供されているフィードの利用を推奨します）。
- アフィリエイト記事には広告であることの明示が必要です（景品表示法のステマ規制、2023年10月施行）。リンクが入った記事には自動でPR表記を付けています。
- AIが書いた記事は事実誤認を含むことがあります。品質チェックで減らしていますがゼロにはできないため、公開された記事を時々読んで確認してください。
- アフィリエイトリンクは仮のURLのまま公開しないでください（例のリンクはコメントアウトしてあります）。
- 収益の保証はありません。

## 開発

```bash
pip install -e '.[dev]'
pytest
```
