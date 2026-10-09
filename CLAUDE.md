# BlendKeep: 作業メモ（Claude 向け）

## 回答ルール（ユーザーの指示）

- 回答に「未確認」「確認していません」を残さない。確認できていない事項があれば、先に調べてから答える。
- 調べても確認できなかった場合は、「未確認」と書かず、試した手段と、確認できなかった具体的な理由を書く。
- 星数・競合の有無・開発者種別（個人/組織）は、推測せずに検索や取得で裏付ける。裏付けた出典は回答末尾に載せる。
- ユーザーの批判には、同意できる点と反論できる点を分けて答える。

## プロジェクト

- Blender 向けの独立 Windows アプリ「BlendKeep」（Python、アドオン不要）。個人開発、GitHub で公開、目標は星を集めること。
- 機能: バージョン管理＋自動バックアップ（核）、レンダー完了通知、サムネイル表示。
- 以前の調査メモ（アイデア選定・競合・X の反応）は、非公開の旧リポジトリ（aru-taigm/newapp）の CLAUDE.md にある。

## 調査・開発の手段メモ

- WebFetch ツールは x.com / dev.to などが遮断される。Bash の curl なら届く。
- 検証用に、scratchpad の venv へ `pip install bpy watchdog`（bpy 5.2.2、Python 3.13）を入れると、実際の .blend を生成して確認できる。bpy の Workbench レンダーはヘッドレスで止まる（Cycles を使う）。
- Qt の画面はオフスクリーン（`QT_QPA_PLATFORM=offscreen`）で `widget.grab()` して確認できる。Linux では `apt-get install libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1` が必要。
- CI の結果は GitHub の MCP ツール（`mcp__github__actions_list` / `get_job_logs`、ToolSearch で読み込む）で確認できる。
- スクリプトは scratchpad に置き、`python3 -I` で実行する。

## 実装メモ

- BlendKeep の実装メモ（2026-10-08、`src/blendkeep/`）:
  - 確認済み（Blender 5.2.2・Linux）: 保存は `x.blend@` に書いて `x.blend` へリネーム、既存は `x.blend1` へ移動。新ヘッダーは `BLENDER17-01v0502`（17バイト）、BHead は32バイト（code, sdna i32, old u64, len i64, nr i64）。`REND`→`TEST`（サムネイル）→`GLOB` の順。サムネイルは幅・高さ(i32)＋RGBA、行は下から順（既定キューブで確認）。bpy のバックグラウンド保存では、サムネイルは起動ファイルの既定のものが入る。
  - 従来形式（BLENDER-v300 等）の読み取りは、仕様どおりの疑似データでのみテスト（実物は未入手）。
- BlendKeep の画面（2026-10-08、`src/blendkeep/gui/`）: PySide6 でトレイ常駐＋履歴ブラウザ＋設定。オフスクリーン（`QT_QPA_PLATFORM=offscreen`）で `widget.grab()` して見た目を確認できる（日本語フォントは IPAGothic が入っている）。Qt の実行には `apt-get install libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1` が必要だった。README のスクリーンショット `docs/screenshot.png` はデモデータ（サムネイルは色を変えて合成）。Windows の実機ではまだ動かしていないので、CI の結果と、ユーザーの実機での確認を待つ。
- CI の結果は GitHub の MCP ツール（`mcp__github__actions_list` / `get_job_logs`、ToolSearch で読み込む）で確認できる。2026-10-08: 最初のコミットで ubuntu/3.13 のテストが1回落ち、原因は監視の取りこぼし（処理中に届いた保存イベントを捨てていた）だった。`generation` カウンタで修正し、再現テストを追加。
- 履歴の圧縮と保存先の移動（2026-10-08）: `objects/<aa>/<sha>.blend.zst`（zstd レベル3）。識別（SHA-256）は圧縮前の内容で行い、Blender が圧縮済みのファイルはそのまま `.blend` で保存。実測（bpy 5.2.2、約23MBの非圧縮シーン）: 8.4MB（36%）、0.18秒。実際の Blender 保存→圧縮→復元（一致）→移動→Blender で開く、まで確認済み。保存先の変更は `SnapshotStore.copy_to`（元は消さない）→ `controller.move_store` → 必要なら `remove_store_files`（BlendKeep が作ったファイルだけ削除）。`apply_config` は保存先の変更を無視する（移動は必ず `move_store` 経由）。
- 教訓（2026-10-08）: Windows の CI で、GUI テストが本物のレジストリ（自動起動の Run キー）を触り、失敗時のモーダルダイアログで止まった。テストは `autostart.is_supported` を無効化して触らせない。止まったテストは `pytest-timeout`（thread 方式）がスタックを出して失敗にする。Run キーは `CreateKeyEx` で、無ければ作る。
- 履歴の削除・メモ・二重起動の防止（2026-10-08）: `SnapshotStore.set_note / delete_snapshots / delete_file_history`、`prune` はメモ付きを削除しない。画面は履歴の複数選択・右クリックメニュー・メモ（QInputDialog）・削除（確認あり、既定は「いいえ」）。二重起動は `gui/single_instance.py`（QLockFile＋QLocalServer）。テストは別プロセスを起動して確認（同一プロセスでの QLockFile の挙動に頼らない）。異常終了後の古いロックの引き継ぎもテスト済み（Linux）。
- 差分保存（2026-10-08、`chunking.py` / `store.py`）: 1MiB 以上の非圧縮 .blend は、ブロック単位で分けたチャンク（`chunks/<aa>/<sha>.chunk`、zstd）で保存し、版どうしで共通部分を共有する。大きなブロック（96KiB以上）はブロック先頭から64KiBごと、小さなブロックは内容で区切って8〜128KiBにまとめる。マニフェストと参照数は SQLite。Blender が圧縮したファイルは分けない。実測（bpy 5.2.2、球20個＋編集8回、1版約16MB）: 8版の合計 129.6MB → 7.1MB（5.5%）、復元はバイト一致、Blender で開ける。設定は `chunk_large_files`（既定オン）。`compact` で既存の丸ごと保存も変換できる。
- 教訓: `BufferedReader.peek` は、バッファに何か入っていれば追加で読まない。先頭の判定には、必要な長さ（64バイト）を先に読んでから渡す。
- レンダー完了通知（2026-10-08）: `blender/blendkeep_bridge.py`（Blender の `scripts/startup` に置く。persistent ハンドラ render_init/post/complete/cancel）が、JSON を `%LOCALAPPDATA%\BlendKeep\events`（環境変数 `BLENDKEEP_EVENTS` で変更）に tmp→rename で置く。`render_events.EventSpool` が1秒ごとに取り込み（書き込み途中の壊れたファイルは60秒経つまで消さない、15分より古いイベントは捨てる）、コントローラが最短時間（既定30秒）で絞ってトレイ通知＋任意で Discord Webhook。導入は `blender_setup`（`BLENDKEEP_BLENDER_CONFIG` で場所を差し替え可）。bpy 5.2.2 で Cycles(CPU)レンダーして完了イベント（frames=3）を確認。bpy の Workbench レンダーはヘッドレスで止まる（Cycles を使う）。`startup` からの自動読み込みは、bpy モジュールでは試せず、公式マニュアルの仕様に頼っている。テストは `tests/conftest.py` の autouse でイベント・Blender 設定の場所を tmp に向ける。conftest.py には `write_blend` があるので上書きしない。
- 教訓: Windows の CI は既定の文字コードが cp1252。テストで日本語を `write_text` / `read_text` するときは `encoding="utf-8"` を必ず付ける。
- 配布（2026-10-08）: `.github/workflows/release.yml`。windows-latest で Nuitka（`--standalone`、フォルダ形式、PySide6 プラグイン、`--windows-console-mode=disable`）→ `BlendKeep.exe --self-test`（画面を作り、watchdog / zstandard / 同梱の Blender 用スクリプトを確かめて終了コード 0）→ zip＋SHA-256 → artifact。`v*` タグのときだけ `gh release create`。`packaging/` を変えたときはブランチでもビルドされる。初回の実行で、ビルド約3分、zip 約33MB、self-test 成功（run 1、コミット 58f3185）。入口は `packaging/blendkeep_gui.py`（相対 import を避ける）、アイコンは `packaging/make_icon.py` で生成した `blendkeep.ico`。未実施: コード署名、winget/Scoop、Defender での実機確認、タグ付けによる公開（ユーザーの許可待ち）。
