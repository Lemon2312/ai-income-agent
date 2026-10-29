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

このリポジトリには、ブログ本体（`site/`）と、GitHub Actions で毎日9時台・18時台（日本時間）に記事を書く仕組みが入っています（一時停止するには `.github/workflows/blog-agent.yml` の `schedule` の3行をコメントにします）。設定はブラウザだけで完了します。

1. **APIキーを登録**: リポジトリの Settings → Secrets and variables → Actions → New repository secret で、名前 `OPENAI_API_KEY`、値に OpenAI のAPIキーを登録
2. **Pages を有効化**: Settings → Pages → Build and deployment の Source を「GitHub Actions」に変更
3. **プルリクエスト作成を許可**: Settings → Actions → General → Workflow permissions で「Allow GitHub Actions to create and approve pull requests」にチェックして Save
4. **試しに1回動かす**: Actions タブ → 「Blog agent」→「Run workflow」

数分後、記事がプルリクエストとして届きます（GitHub アプリならスマホに通知が来ます）。

- **公開する**: プルリクエストを「Merge」すると、数分でブログ（`https://<ユーザー名>.github.io/<リポジトリ名>/`）に公開されます
- **公開しない**: 「Close」するだけ。同じテーマは二度と書きません
- **直してから公開する**: 「Files changed」タブで記事を編集してからマージ

Actions タブの「Run workflow」で手動実行したときは、`max_articles_per_day`（1日の記事数の上限）を気にせず1本書きます。手動で作った記事は上限に数えないので、試しに動かしても自動実行の枠は減りません（1日のAI利用料の上限 `daily_llm_budget_usd` はどちらにもかかります）。

品質に納得したら、`agent.toml` の `deploy = "github-pr"` を `deploy = "git"` に変えると、確認なしの完全自動公開になります。ジャンル、1日の本数、品質基準、アフィリエイトリンクなども `agent.toml` で変更できます（GitHub 上で直接編集できます）。

収益（アフィリエイト報酬など）は、Actions タブ →「Record revenue」→「Run workflow」で金額とメモを入れると記録できます（スマホの GitHub アプリからも可）。種類を `report` にすると、記録はせずに収支レポートだけを表示します。記録は `.agent/` に保存されるので、**リポジトリを private にしてから**使ってください。

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

ジャンルは `niche` / `audience` で決まります（このリポジトリの GitHub 運用では `agent.toml` の「量子コンピュータのやさしい解説」、手元用のひな形 `config.toml` では「フリーランスのためのAI時短術」）。`guidelines` にジャンル固有の執筆ルールを書くと、企画・執筆・採点のすべてでAIに渡され、違反した記事は0点になります。

### 基本解説シリーズ（`source = "basics"`、いまの設定）

`source = "basics"` にすると、`topics` に並べたタイトルを上から順に1本ずつ、量子コンピュータのしくみを1回1テーマで解説します。シリーズを書き終わると止まり、新しいタイトルを勝手に考えることはしません（続けるときは `topics` に足すか、`source = "arxiv"` で論文解説に戻します）。

1. 記事は「（運営者の疑問とタネリスの紹介）→ ひとことで言うと → 身近なものにたとえると → しくみを見てみよう → もう少しくわしく → よくある誤解 → まとめ → クイズでチェック → 用語ミニ辞典」の型で書き、最後にシリーズの案内が付きます。URL は `…-quantum-basics-01` のように回の番号になります
2. AIには、シリーズの何回目か・これまでの回・次の回のタイトルを毎回渡すので、前の回の説明をくり返さず、最後に次の回へつなぎます
3. 論文解説と同じ部品に加えて、読者がさわって試せる部品を使います（設定がおかしいものは自動で取り除きます）
   - `{{< qubit theta="90" >}}`: 量子ビットの矢印を傾けて、0 と 1 が出る確率を見たり、測定を試したりできる
   - `{{< circuit qubits="2" gates="H 0; CNOT 0 1; M" >}}`: 1〜3量子ビットの回路（H・X・Z・CNOT・最後に測定）を1ステップずつ動かし、結果の確率と符号の変化を見られる。サイトの中で実際に計算するので、干渉や量子もつれの結果も正しく表示されます
   - `{{< growth >}}`: 量子ビットの数を増やすと、状態を書き表すのに必要な数（2のn乗）がどう増えるかを見られる
   - `{{< flip front="よくある誤解" real="…" back="本当は" analogy="…" >}}`: 裏返せるカードの見出しを変えて、誤解と正しい説明を見せる
4. AI編集長は、教科書的に確立した内容かどうか、「全部の答えを同時に計算する」のような誤解を招く説明がないか、部品の設定と本文の説明が合っているかを採点します

### 論文解説モード（`source = "arxiv"`）

`source = "arxiv"` にすると、AIはテーマの題名からではなく、実際の論文を読んで解説記事を書きます。

1. `papers` に arXiv 番号（例: `"2408.13687"`）があれば、それを最優先で上から順に記事にします
2. リストが空なら、`arxiv_query` に合う「最近 `recent_days` 日以内」の論文から選びます。`news_feeds` のニュースで紹介された論文・有名な論文誌への掲載・大規模な共同研究などの「話題のサイン」が多い順に並べ、ニュースの見出しといっしょにAIに見せて、初心者向けの解説に合うものを選ばせます
3. 論文の要旨と本文（arXiv の HTML 版があるもの）を取得し、「ひとことで言うと → なぜこの研究が必要？ → 何をしたの？ → しくみをもう少しくわしく → 何がすごいの？ → まだできないこと・課題 → これからどうなる？ → クイズでチェック → 用語ミニ辞典」の型で、全体で `min_chars`（約5000字）を目安に書きます。最後に元の論文へのリンクが付きます
4. 記事には、要約ボックス（`summary`）、ステップが順番に光る手順の図（`flow`）、数字カード（`stats` / `stat`、スクロールで数字が動く）、伸びる棒グラフ（`bars` / `bar`）、タップで裏返るたとえカード（`flip`）、大学生の姿の運営者（`hugo.toml` の `readerName`）の質問と、博士帽をかぶったタネリスの答え（`ask` / `tanerisu`、運営者は驚く・考えこむ・喜ぶの表情つき）、タップで答えが開くクイズ（`quiz`）、用語カード（`term`）を使います。AIが書き間違えた部品は自動で取り除くので、サイトのビルドは壊れません
   - 人が手で仕上げる記事では、記事をフォルダ（`site/content/posts/<記事名>/index.md`）にして、同じフォルダに `fig-<名前>.html`（図のHTML）・`figures.css`・`figures.js` を置くと、本文の `{{< htmlfig "<名前>" >}}` の位置に動く図やアニメーションを入れられます（例: `site/content/posts/2026-10-29-arxiv-2610-35227/`）。この部品はAIには使わせません
5. AI編集長は、記事を元の論文と照らし合わせて採点します。論文にない成果や数値が書かれていると不合格になります

同じ論文は二度書きません。不合格になった論文は `max_attempts_per_topic` 回まで書き直し、それでもダメなら次の論文に進みます。

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
