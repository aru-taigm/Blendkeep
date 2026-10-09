# 公開用の文面（下書き）

GitHub の設定（About）と、紹介の投稿の下書き。リポジトリを公開にしたあとで使う。

## GitHub の About

- 説明（Description）: `Blender の .blend を保存のたびに自動で履歴化する Windows 常駐ツール（アドオン不要・差分保存・レンダー完了通知）`
- Website: （空でよい）
- Topics: `blender` `blender-addon-alternative` `backup` `version-control` `windows` `pyside6` `python` `tray-app` `3d` `render-notification`

## X（日本語）の下書き

### 案1（困りごと起点）
```
Blenderで「上書き保存して戻れない」「落ちて消えた」をなくすツールを作りました。

・保存するたびに自動で履歴を残す（アドオン不要）
・サムネイル付きで一覧→1クリックで戻せる
・重いシーンは差分保存で省スペース（16MB×8回=7MB）
・レンダー完了をトレイ/Discordに通知

Windows / MIT
https://github.com/aru-taigm/BlendKeep
#Blender #b3d
```
（デモ GIF `docs/demo.gif` を添付）

### 案2（数字起点）
```
Blenderのバックアップ、.blend1 の1世代だけで足りてますか？

BlendKeepは保存のたびに履歴化します。
重いシーン（1版16MB）を8回保存しても合計7.1MB。差分だけ保存するので。
```

## 英語の短い投稿（Blender Artists / r/blender 向け）
```
BlendKeep: automatic version history for .blend files (Windows tray app, no add-on).
Watches your folders, keeps a thumbnail-browsable history, stores big files as deltas
(8 saves of a 16 MB scene = 7 MB), and notifies you (tray/Discord) when a render finishes. MIT.
```
投稿先のルール（自己宣伝の可否）は、投稿する前に各コミュニティの規約で確かめる。

## 公開前のチェックリスト

- [x] Windows 実機で起動・履歴作成・レンダー通知を確認した
- [ ] タグ `v0.1.0` を作って、Releases に zip と .sha256 を載せる（下の手順の 0）
- [ ] GitHub の About と Topics を設定した
- [ ] 実機のスクリーンショットを README のデモと差し替えた（任意）

## 公開する手順（リポジトリの設定だけ）

0. GitHub のリポジトリ → Releases → Draft a new release → Choose a tag に `v0.1.0` と入力して「Create new tag」→ Target は `main` → Publish release。説明欄には `docs/release-notes/v0.1.0.md` の中身を貼る。
   タグが作られると、自動で配布物のビルドが走り（約5分）、zip と .sha256 が同じ Release に付きます（Actions の Release が緑になるまで待つ）
1. GitHub のリポジトリ → Settings → General → 一番下の Danger Zone → **Change visibility** → Public
2. 同じ画面の上のほうの About（歯車）に、上の説明文と Topics を貼る
3. Releases に `v0.1.0`（zip と .sha256）があることを確かめる
4. 上の投稿文に、デモ GIF を添えて投稿する

公開すると、履歴（コミット）と `CLAUDE.md`（作業メモ）も誰でも読めるようになります。
