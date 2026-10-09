"""コマンドライン。常駐アプリの画面ができるまでは、これが入口になる。"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import Config, default_config_path
from .formatting import format_size
from .store import MigrationResult, SnapshotStore, remove_store_files
from .watcher import BlendWatcher


def _cmd_watch(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    if args.dir:
        config.watch_dirs = [str(Path(d).resolve()) for d in args.dir]
    if not config.watch_dirs:
        print(
            "監視するフォルダがありません。"
            "--dir で指定するか、`blendkeep add-dir` で登録してください。"
        )
        return 2
    store = SnapshotStore(
        config.store_dir,
        compress=config.compress_history,
        chunk_large_files=config.chunk_large_files,
    )
    watcher = BlendWatcher(
        config,
        store,
        on_snapshot=lambda s: print(f"履歴を作成しました: {s.source_path} (id={s.id})"),
    )
    watcher.start()
    print("監視中です（Ctrl+C で終了）:")
    for d in config.watch_dirs:
        print(f"  {d}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()
        store.close()
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    store = SnapshotStore(
        config.store_dir,
        compress=config.compress_history,
        chunk_large_files=config.chunk_large_files,
    )
    snap = store.add(args.file, note=args.note)
    store.close()
    if snap is None:
        print("直前の履歴と同じ内容のため、追加しませんでした。")
    else:
        print(f"履歴を作成しました: id={snap.id}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    store = SnapshotStore(config.store_dir)
    snaps = store.list(args.file, limit=args.limit)
    store.close()
    if not snaps:
        print("履歴はありません。")
        return 0
    for s in snaps:
        when = datetime.fromtimestamp(s.taken_at).strftime("%Y-%m-%d %H:%M:%S")
        note = f"  # {s.note}" if s.note else ""
        print(f"{s.id:>5}  {when}  {format_size(s.size):>10}  {s.source_path}{note}")
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    store = SnapshotStore(config.store_dir)
    try:
        dest = store.restore(args.id, args.to)
    except (KeyError, FileExistsError) as e:
        print(str(e), file=sys.stderr)
        return 1
    finally:
        store.close()
    print(f"取り出しました: {dest}")
    return 0


def _cmd_thumbnail(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    store = SnapshotStore(config.store_dir)
    try:
        snap = store.get(args.id)
        if snap is None:
            print(f"履歴 {args.id} は見つかりません", file=sys.stderr)
            return 1
        thumb = store.read_thumbnail(snap.sha256)
    finally:
        store.close()
    if thumb is None:
        print(
            "このファイルにはサムネイルがありません（「ファイルプレビュー」が無効の可能性があります）。"
        )
        return 1
    args.out.write_bytes(thumb.to_png())
    print(f"保存しました: {args.out} ({thumb.width}x{thumb.height})")
    return 0


def _cmd_note(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    store = SnapshotStore(config.store_dir)
    try:
        ok = store.set_note(args.id, None if args.clear else args.text)
    finally:
        store.close()
    if not ok:
        print(f"履歴 {args.id} は見つかりません", file=sys.stderr)
        return 1
    print("メモを消しました。" if args.clear else "メモを付けました。")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    if not args.yes:
        print("履歴を削除します。元の .blend ファイルには影響しません。")
        print("実行するには --yes を付けてください。", file=sys.stderr)
        return 1
    config = Config.load(args.config)
    store = SnapshotStore(config.store_dir)
    try:
        result = store.delete_snapshots(args.ids)
    finally:
        store.close()
    print(f"{result.count} 件を削除しました（{format_size(result.freed_bytes)} を空けました）")
    return 0 if result.count else 1


def _cmd_compact(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    store = SnapshotStore(config.store_dir)
    try:
        before = store.stats()
        saved = store.compact(lambda done, total, _name: _print_progress(done, total))
        after = store.stats()
    finally:
        store.close()
    print(
        f"\n圧縮しました: {format_size(before.stored_bytes)} → {format_size(after.stored_bytes)}"
        f"（{format_size(saved)} 節約）"
    )
    return 0


def _print_progress(done: int, total: int) -> None:
    percent = int(done * 100 / total) if total else 100
    print(f"\r  {percent:3d}%", end="", flush=True)


def _cmd_move_store(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    new_dir = str(Path(args.dir).resolve())
    if new_dir == str(Path(config.store_dir).resolve()):
        print("すでにその場所に保存しています。")
        return 0
    store = SnapshotStore(config.store_dir)
    old_dir = store.root
    try:
        result: MigrationResult = store.copy_to(
            new_dir, lambda done, total, _name: _print_progress(done, total)
        )
    except (ValueError, OSError) as error:
        print(f"\n移せませんでした: {error}", file=sys.stderr)
        return 1
    finally:
        store.close()
    config.store_dir = new_dir
    config.save(args.config)
    if result.adopted:
        print(f"\n移動先にあった履歴を使います: {new_dir}\n元の履歴は残してあります: {old_dir}")
        return 0
    print(f"\n履歴を移しました: {new_dir}")
    if args.delete_old:
        freed = remove_store_files(old_dir)
        print(f"元の場所を削除しました（{format_size(freed)}）")
    else:
        print(f"元の履歴は残してあります: {old_dir}（--delete-old で削除できます）")
    return 0


def _cmd_add_dir(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    path = str(Path(args.dir).resolve())
    if path not in config.watch_dirs:
        config.watch_dirs.append(path)
    saved = config.save(args.config)
    print(f"登録しました: {path}\n設定ファイル: {saved}")
    return 0


def _cmd_blender_setup(args: argparse.Namespace) -> int:
    from . import blender_setup

    targets = blender_setup.find_targets()
    if not targets:
        print(f"Blender の設定フォルダが見つかりません: {blender_setup.blender_config_root()}")
        print("一度 Blender を起動してから、やり直してください。")
        return 1
    if args.action == "install":
        for t in blender_setup.install(targets):
            print(f"導入しました: Blender {t.version} → {t.script_path}")
        print("Blender を起動し直すと有効になります。")
    elif args.action == "uninstall":
        removed = blender_setup.uninstall(targets)
        for t in removed:
            print(f"外しました: Blender {t.version}")
        if not removed:
            print("導入されているものはありません。")
    else:
        for t in targets:
            state = "導入済み" if t.installed else "未導入"
            if t.installed and not blender_setup.is_up_to_date(t):
                state += "（古い版。install で更新できます）"
            print(f"Blender {t.version}: {state}")
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    print(f"設定ファイル: {args.config or default_config_path()}")
    print(f"履歴の保存先: {config.store_dir}")
    print(f"圧縮して保存: {'する' if config.compress_history else 'しない'}")
    print(f"大きなファイルは差分保存: {'する' if config.chunk_large_files else 'しない'}")
    print(
        f"レンダー完了の通知: {'する' if config.notify_on_render else 'しない'}"
        f"（{int(config.render_min_seconds)}秒以上）"
    )
    print(f"Discord への送信: {'する' if config.discord_webhook_url else 'しない'}")
    print("監視フォルダ:")
    for d in config.watch_dirs or ["(なし)"]:
        print(f"  {d}")
    store = SnapshotStore(config.store_dir)
    stats = store.stats()
    store.close()
    print(
        f"履歴: {stats.snapshots} 件、保存サイズ {format_size(stats.stored_bytes)}"
        f"（圧縮前 {format_size(stats.original_bytes)}）"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blendkeep", description="Blender ファイルの自動履歴化")
    parser.add_argument("--version", action="version", version=f"blendkeep {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="設定ファイルのパス")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("watch", help="フォルダを監視して自動で履歴を作る")
    p.add_argument("--dir", action="append", help="監視するフォルダ（複数指定可）")
    p.set_defaults(func=_cmd_watch)

    p = sub.add_parser("snapshot", help="1つのファイルの履歴を今すぐ作る")
    p.add_argument("file", type=Path)
    p.add_argument("--note", default=None)
    p.set_defaults(func=_cmd_snapshot)

    p = sub.add_parser("list", help="履歴の一覧")
    p.add_argument("file", nargs="?", type=Path)
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("restore", help="履歴を取り出す（上書きはしない）")
    p.add_argument("id", type=int)
    p.add_argument("--to", type=Path, default=None, help="取り出し先のパス")
    p.set_defaults(func=_cmd_restore)

    p = sub.add_parser("thumbnail", help="履歴のサムネイルを PNG で保存する")
    p.add_argument("id", type=int)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=_cmd_thumbnail)

    p = sub.add_parser("note", help="履歴にメモを付ける（メモ付きは自動で削除されない）")
    p.add_argument("id", type=int)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("text", nargs="?", default=None)
    group.add_argument("--clear", action="store_true", help="メモを消す")
    p.set_defaults(func=_cmd_note)

    p = sub.add_parser("delete", help="履歴を削除する")
    p.add_argument("ids", type=int, nargs="+")
    p.add_argument("--yes", action="store_true", help="確認なしで削除する")
    p.set_defaults(func=_cmd_delete)

    p = sub.add_parser("compact", help="いまある履歴を圧縮して、容量を減らす")
    p.set_defaults(func=_cmd_compact)

    p = sub.add_parser("move-store", help="履歴の保存先を別のフォルダへ移す")
    p.add_argument("dir", type=Path)
    p.add_argument("--delete-old", action="store_true", help="移した後、元の場所を削除する")
    p.set_defaults(func=_cmd_move_store)

    p = sub.add_parser("add-dir", help="監視フォルダを登録する")
    p.add_argument("dir", type=Path)
    p.set_defaults(func=_cmd_add_dir)

    p = sub.add_parser("blender-setup", help="Blender にレンダー完了通知の起動スクリプトを入れる")
    p.add_argument("action", choices=["install", "uninstall", "status"])
    p.set_defaults(func=_cmd_blender_setup)

    p = sub.add_parser("config", help="現在の設定を表示する")
    p.set_defaults(func=_cmd_config)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
