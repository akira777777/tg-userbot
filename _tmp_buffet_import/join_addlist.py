"""Добавить чаты из папки-инвайта (t.me/addlist/<slug>) в рассылку.

Механика: аккаунт делает CheckChatlistInvite (предпросмотр состава, ничего
не меняет), затем JoinChatlistInvite — ОДНИМ вызовом вступает во все чаты
папки, включая приватные (единственный способ вступить в приватный чат
папки без отдельной invite-ссылки). Ответ Updates.chats содержит полные
объекты чатов — штатный upsert_mailing_group регистрирует их в базе
с honest edges этого аккаунта (classify_broadcast + refresh_group_flags).

Побочный эффект: у аккаунта появляется сама папка («rozyob») в списке
чатов; удалить её в клиенте можно руками — членство в чатах сохранится.

Запуск:
  py -3.15 _tmp_buffet_import/join_addlist.py --account work33 --dry-run
  py -3.15 _tmp_buffet_import/join_addlist.py --account work12
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from telethon.tl.functions.chatlists import (  # noqa: E402
    CheckChatlistInviteRequest,
    JoinChatlistInviteRequest,
)
from telethon.tl.types import InputPeerChannel, PeerChannel  # noqa: E402
from telethon.tl.types.chatlists import ChatlistInviteAlready  # noqa: E402

from bot import connect, get_client, require_auth, upsert_mailing_group  # noqa: E402


def _peer_inputs(peers) -> list[InputPeerChannel]:
    return [InputPeerChannel(p.channel_id, p.access_hash)
            for p in peers if isinstance(p, PeerChannel)]


async def run(account: str, slug: str, dry: bool) -> None:
    client = get_client(account)
    await require_auth(client)
    try:
        invite = await client(CheckChatlistInviteRequest(slug=slug))
        title = getattr(getattr(invite, "filter", None), "title", "?") or "?"
        state = "уже добавлена" if isinstance(invite, ChatlistInviteAlready) \
            else "не добавлена"
        missing = list(getattr(invite, "missing_peers", None) or [])
        already = list(getattr(invite, "already_peers", None) or [])
        preview = list(getattr(invite, "chats", None) or [])
        print(f"папка «{title}» ({state}): новых чатов {len(missing)}, "
              f"уже в аккаунте {len(already)}")
        for ent in preview:
            if not hasattr(ent, "title"):
                continue
            kind = "канал" if getattr(ent, "broadcast", False) else "чат"
            print(f"  {ent.id} {kind} «{ent.title}» "
                  f"@{getattr(ent, 'username', None) or '-'}")
        if dry:
            print("dry-run: без вступления и записи в базу")
            return
        peers = _peer_inputs(missing)
        if not peers:
            print("вступать не во что: все чаты папы уже в аккаунте")
            return
        updates = await client(JoinChatlistInviteRequest(slug=slug, peers=peers))
        chats = [c for c in (getattr(updates, "chats", None) or [])
                 if hasattr(c, "title")]
        conn = connect()
        fresh = 0
        for ent in chats:
            added, can, why = upsert_mailing_group(conn, account, ent)
            fresh += int(added)
            mark = "" if can else f" (не цель: {why})"
            print(f"  + {ent.id} «{ent.title}» "
                  f"@{getattr(ent, 'username', None) or '-'}{mark}")
        # Чаты, не вернувшиеся в Updates.chats, доберёт штатный groups --sync.
        conn.commit()
        conn.close()
        print(f"записано групп: {len(chats)} (новых в реестре: {fresh})")
    finally:
        await client.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="yWjV_bFQgiYyZWVl",
                    help="хвост ссылки t.me/addlist/<slug>")
    ap.add_argument("--account", default="work33", help="аккаунт-исполнитель")
    ap.add_argument("--dry-run", action="store_true",
                    help="только предпросмотр состава папки")
    args = ap.parse_args()
    try:
        asyncio.run(run(args.account, args.slug, args.dry_run))
    except SystemExit as exc:
        # get_client выходит с «Слот … занят»: код 42 — сигнал наблюдателю
        # «сейчас занято, попробуй позже», а не ошибка скрипта.
        if "Слот" in str(exc):
            print("SLOT_BUSY")
            sys.exit(42)
        # flood_guard выходит с «… в cooldown до …»: код 43 — аккаунт
        # сегодня не годится, наблюдатель переходит к следующему.
        if "cooldown" in str(exc):
            print("FLOOD_COOLDOWN")
            sys.exit(43)
        raise


if __name__ == "__main__":
    main()
