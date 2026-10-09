# BlendKeep

**Blender の保存ファイルを、保存するたびに自動で履歴に残す Windows 常駐ツール。**
「上書きして戻れない」「落ちて消えた」「納品前の版に戻したい」を、アドオンなしで解決します。

English: BlendKeep is a Windows tray app that automatically keeps a version history of your `.blend` files
every time you save. No Blender add-on needed; it just watches your folders. Large files are stored as
deltas (8 saves of a 16 MB scene = 7 MB), and it can notify you (tray / Discord) when a render finishes.
See [README.en.md](README.en.md).

![BlendKeep のデモ](docs/demo.gif)

*デモ用のデータを Linux 上で描画した画面です（通知は合成、サムネイルは見本の絵）。*

- **入れてすぐ効く**: フォルダを選ぶだけ。Blender 側の設定は要りません。複数のフォルダ・ドライブに分かれたプロジェクトも、まとめて見張れます
- **軽い**: 重いシーンは変わった部分だけ保存（差分保存）。小さいファイルは圧縮
- **安全**: 取り出しは既存のファイルを上書きしません。元の .blend は一切いじりません
- **レンダー完了をトレイ / Discord に通知**（任意）

> **状態: v0.1.0（初版）。** Windows の実機、Linux 上の実際の Blender 5.2.2、GitHub Actions の Windows で動作確認しています。
> 不具合や要望は Issues へ。

## できること（現在）

- 指定したフォルダ（複数可）の中の `.blend` を監視し、保存が終わったら履歴を作る
  - 連続して保存しても、最後の状態だけを残す（`debounce_seconds` / `min_interval_seconds`）
  - 直前と中身が同じなら作らない。同じ内容は1つしか保存しない（SHA-256）
  - Blender が作る `.blend1` / `.blend@` は対象外
- 画面（タスクトレイに常駐）: ファイルごとの履歴をサムネイル付きで一覧、「この版を取り出す」、
  一時停止、設定（監視フォルダ・残す件数・通知・Windows ログイン時の自動開始）
- **履歴の削除とメモ**: 履歴を選んで、削除（複数選択可）やメモの編集ができます（右クリックでも）。
  **メモを付けた履歴は、古くなっても自動では削除されません**（残す件数にも数えません）。納品版などに使えます。
  削除しても、元の .blend ファイルには影響しません
- **二重起動の防止**: すでに起動しているときに、もう一度起動すると、起動済みのものの画面を前に出して終了します
  （ログイン時の自動開始のあとに手動で起動しても、重複して監視しません）
- **履歴の圧縮**: Blender が圧縮していない .blend は zstd で圧縮して保存します。
  Blender 5.2.2 で作った約23MB（非圧縮）の重いメッシュのシーンが、約8.4MB（36%）になりました（Blender 自身の圧縮と同じ割合）。
  テクスチャなどを含むファイルでは、割合は変わります
- **履歴の保存先の変更**: 設定画面から、履歴をまるごと別のフォルダ（別のドライブでも）へ移せます。
  移したあと、元の場所の履歴を削除するかを選べます（既定は残します）
- 履歴の取り出し（**既存のファイルは上書きしない**）。取り出すときは、内容が記録と一致するか照合します
- **差分保存**: 1MB 以上の .blend は、変わった部分だけを保存します。
  球20個のシーン（1版約16MB）を8回保存すると、合計129.6MBが7.1MB（5.5%）で済みました（Blender 5.2.2 で実測、復元はバイト一致）。
  設定でオフにでき、`blendkeep compact` で既存の履歴も変換できます。
- **レンダー完了通知**: トレイから「レンダーが完了しました（ファイル名・フレーム数・所要時間）」と知らせます。
  短いレンダー（既定30秒未満）は知らせません。Discord の Webhook を設定すると、スマホにも届きます。
  使うには、設定画面の「Blender に導入する」（または `blendkeep blender-setup install`）で、
  Blender のユーザー設定フォルダの `scripts/startup` に小さなスクリプトを1つ置きます（アドオンの有効化は不要。
  外すときは「Blender から外す」）。実際の Blender 5.2.2 でレンダーして、完了イベントが届くことを確認しています。
  起動フォルダからの自動読み込みは Blender のマニュアルの仕様に従うもので、bpy モジュールでは試せません。
- `.blend` に埋め込まれたサムネイルを PNG で取り出す（非圧縮 / gzip / zstd、Blender 5.x の新形式と従来形式）

## 使い方

Python 3.10 以上が必要です。

### 画面から使う

```powershell
pip install -e ".[gui]"
blendkeep-gui
```

初回は設定が開くので、Blender のファイルがあるフォルダ（複数可）を追加します。
ウィンドウを閉じてもタスクトレイに常駐して監視を続けます。終了はトレイのメニューから行います。

### コマンドラインから使う

```powershell
pip install -e .

# 監視するフォルダを登録（何回でも追加できます）
blendkeep add-dir D:\Blender\ProjectA
blendkeep add-dir E:\Work\3D

# 監視を開始（Ctrl+C で終了）
blendkeep watch

# 履歴を見る / 取り出す / サムネイルを保存する
blendkeep list
blendkeep list D:\Blender\ProjectA\scene.blend
blendkeep restore 12                    # scene.restored-12.blend として元の場所に取り出す
blendkeep restore 12 --to D:\tmp\x.blend
blendkeep thumbnail 12 --out thumb.png

# メモと削除
blendkeep note 12 "納品版"              # メモを付ける（--clear で消す）。メモ付きは自動で削除されない
blendkeep delete 12 13 --yes            # 履歴を削除する（元の .blend には影響しない）

# 容量まわり
blendkeep config                        # 履歴の件数と使っている容量も表示
blendkeep compact                       # いまある履歴を圧縮する
blendkeep move-store E:\BlendKeepStore  # 履歴の保存先を移す（--delete-old で元を削除）
```

## 履歴はどこに保存される？

既定では `%LOCALAPPDATA%\BlendKeep\store`（`C:\Users\<ユーザー名>\AppData\Local\BlendKeep\store`）です。
設定画面の「保存先を変更…」か `blendkeep move-store` で変えられます。設定ファイルは `%APPDATA%\BlendKeep\config.json` です。

```
store\
├── index.db                        履歴の一覧（いつ・どのファイル・メモ）
└── objects\
    └── 3f\3fa9c1…（SHA-256）.blend.zst   履歴の本体（圧縮済み）
```

履歴の本体のファイル名は、内容のハッシュ値です。どれがどの版かは `index.db` が覚えているので、
取り出すときは画面の「この版を取り出す…」か `blendkeep restore` を使ってください。

## 仕組み

アドオン型（Blender の中で動く）と違い、BlendKeep は Blender とは別のプログラムです。
Blender が `scene.blend@` に書き込んでから `scene.blend` に名前を変える、という保存の流れを
Blender 5.2.2（Linux）で確認していますが、この流れには依存していません。
`.blend` への作成・更新・名前変更のイベントを集め、ファイルの大きさと更新時刻が一定時間変わらなく
なったら保存完了とみなします。

Blender が閉じていても、落ちた後でも履歴は残ります。反面、Blender の内部情報
（オブジェクトの数など）は見えません。

## ロードマップ

- [x] タスクトレイ常駐と履歴の画面（サムネイル付き、PySide6）
- [x] 画面の二重起動の防止、履歴の削除・メモの編集
- [x] 保存データの圧縮、履歴の保存先の変更
- [x] 大きな `.blend` に向けた差分保存
- [x] レンダー完了通知（Blender の起動スクリプト＋トレイ通知、Discord 通知）
- [x] 配布（Nuitka のフォルダ形式。GitHub Actions で自動ビルド、タグを付けると Releases に公開）
- [ ] コード署名・winget / Scoop への登録
- [x] Windows 上での実機確認

## 実行ファイルでの使い方（Windows）

Releases の `BlendKeep-vX.Y.Z-windows.zip` を展開して、中の `BlendKeep.exe` を実行します（インストール不要）。
同じ場所の `.sha256` ファイルで、ダウンロードが壊れていないか確かめられます
（PowerShell: `Get-FileHash BlendKeep-*.zip`）。コード署名はまだしていないため、初回は
Windows の SmartScreen が警告を出すことがあります。

## 開発

```bash
pip install -e ".[gui,dev]"
ruff check .
QT_QPA_PLATFORM=offscreen pytest
```

`tests/data/blender-5.2.2-compressed.blend` は Blender 5.2.2 が実際に保存した空のシーンです。

## ライセンス

MIT
