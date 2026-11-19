"""Оркестр 11 аккаунтов: чистые функции без сети.

Здесь только планирование и валидация — их легко покрыть тестами:
профили (имя/фамилия/о себе/аватарка), общий набор групп, проверки на
бота и раскладка целей без пересечений. Сетевые вызовы живут в bot.py.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

# Ограничения Telegram (превышение = RPC_ERROR, сообщение не сохранится).
NAME_LIMIT = 64
ABOUT_LIMIT = 70
PHOTO_MIN_SIDE = 320      # официальный минимум для фото профиля
PHOTO_MAX_BYTES = 5 * 1024 * 1024
AVATAR_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

# --- проверки на бота -------------------------------------------------------
# Нажимаем только кнопки, у которых текст явно про проверку. Всё остальное
# («Выйти», «Report», «Оплатить») — не наша добыча, их трогать нельзя.
GATE_BUTTON_WHITELIST = re.compile(
    r"^\s*(я\s+не\s+робот|я\s+не\s+бот|проверить(\s+меня)?|подтвердить|"
    r"начать(\s+проверку)?|продолжить|пройти(\s+проверку)?|"
    r"i('|\s*a)m\s+not\s+a\s+(robot|bot)|verify(\s+me)?|start|continue|confirm)\s*[!.…]*\s*$",
    re.IGNORECASE,
)
GATE_BUTTON_BLACKLIST = re.compile(
    r"(выйти|покинуть|leave|exit|report|пожаловат|жалоб|оплат|pay|buy|купить|"
    r"подписат|subscribe|unsubscribe|отпис|block|заблок|удалить|delete|stop|стоп)",
    re.IGNORECASE,
)
GATE_URL_HINTS = re.compile(r"(captcha|recaptcha|hcaptcha|turnstile|check)", re.IGNORECASE)


@dataclass(frozen=True)
class Profile:
    """Строка profiles.csv: имя, фамилия, о себе. Пустые поля = не менять."""

    name: str = ""
    last_name: str = ""
    about: str = ""

    def normalized(self) -> "Profile":
        return Profile(*(_clean(v) for v in (self.name, self.last_name, self.about)))

    def empty(self) -> bool:
        return not (self.name or self.last_name or self.about)


@dataclass
class Identity:
    """Что применять к одному аккаунту."""

    account: str
    profile: Profile
    avatar: str | None = None


@dataclass
class JoinPlan:
    """Кто в какую группу ещё не вошёл."""

    group_id: int
    title: str
    username: str
    missing: list[str] = field(default_factory=list)
    link_needed: bool = False


@dataclass(frozen=True)
class ButtonCandidate:
    """Кнопка, которую можно безопасно нажать."""

    row: int
    col: int
    text: str


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def truncate(value: str, limit: int) -> str:
    """Обрезка по лимиту без разрыва символа (эмодзи — 1 символ в Python)."""
    value = str(value or "")
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def parse_profile_csv(text: str) -> list[Profile]:
    """Разбор profiles.csv: по строке на аккаунт, разделитель «;» или «,».

    Пустые строки и строки-комментарии (#) пропускаются. Порядок строк =
    порядок аккаунтов из реестра.
    """
    out: list[Profile] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in re.split(r"\s*[;,]\s*", line, maxsplit=2)]
        while len(parts) < 3:
            parts.append("")
        out.append(Profile(*parts[:3]).normalized())
    return out


def validate_profile(profile: Profile) -> tuple[Profile, list[str]]:
    """Обрезать под лимиты Telegram и вернуть предупреждения.

    Пустое имя Telegram не примет — оставляем пустым и сообщаем вызывающему.
    """
    warnings: list[str] = []
    name = truncate(profile.name, NAME_LIMIT)
    last = truncate(profile.last_name, NAME_LIMIT)
    about = truncate(profile.about, ABOUT_LIMIT)
    if len(profile.name) > NAME_LIMIT:
        warnings.append(f"имя>{NAME_LIMIT} обрезано")
    if len(profile.about) > ABOUT_LIMIT:
        warnings.append(f"о себе>{ABOUT_LIMIT} обрезано")
    if any(unicodedata.category(ch) == "Cc" for ch in name + last + about):
        name = re.sub(r"[\x00-\x1f\x7f]", "", name)
        last = re.sub(r"[\x00-\x1f\x7f]", "", last)
        about = re.sub(r"[\x00-\x1f\x7f]", "", about)
        warnings.append("управляющие символы удалены")
    return Profile(name, last, about), warnings


def assign_identities(
    accounts: Sequence[str],
    profiles: Sequence[Profile],
    avatars: Sequence[str],
) -> list[Identity]:
    """Сопоставить аккаунты с профилями и аватарками по порядку.

    Профилей больше, чем аккаунтов — это ошибка ввода: вызывающий увидит
    лишние строки через leftover_profiles. Меньше — у хвоста профиль пустой.
    """
    out: list[Identity] = []
    for index, account in enumerate(accounts):
        profile = profiles[index] if index < len(profiles) else Profile()
        avatar = avatars[index] if index < len(avatars) else None
        out.append(Identity(account, profile, avatar))
    return out


def leftover_profiles(accounts: Sequence[str], profiles: Sequence[Profile]) -> list[Profile]:
    """Строки, для которых не нашлось аккаунта (обычно ошибка в CSV)."""
    return list(profiles[len(accounts):])


def validate_avatar(path: str) -> list[str]:
    """Проверить файл-аватарку по расширению и размеру (без чтения картинки).

    Полную проверку растровых размеров делает вызывающий через Pillow/Telethon;
    здесь дешёвый отсев того, что Telegram точно отвергнет.
    """
    problems: list[str] = []
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return [f"нет файла {p.name}"]
    if p.suffix.lower() not in AVATAR_SUFFIXES:
        problems.append(f"{p.name}: формат {p.suffix} — нужен jpg/png/webp")
    if p.stat().st_size > PHOTO_MAX_BYTES:
        problems.append(f"{p.name}: {p.stat().st_size} байт — лимит 5 МБ")
    return problems


def is_public_group(row: Mapping[str, object]) -> bool:
    """Публичная группа = есть @username (вступить можно через API)."""
    return bool(str(row.get("username") or "").strip())


def plan_joins(
    groups: Iterable[Mapping[str, object]],
    members: Mapping[str, Iterable[int]],
    accounts: Sequence[str],
) -> list[JoinPlan]:
    """Для каждой группы — список аккаунтов, которых в ней ещё нет.

    members: account -> множество group_id, где он уже состоит
    (из account_groups). Приватную группу без invite_link помечаем
    link_needed: API не умеет вступать без ссылки.
    """
    plans: list[JoinPlan] = []
    for row in groups:
        gid = int(row["id"])  # type: ignore[arg-type]
        have = {account for account in accounts if gid in set(members.get(account, ()))}
        missing = [account for account in accounts if account not in have]
        public = is_public_group(row)
        link = str(row.get("invite_link") or "").strip()
        plans.append(JoinPlan(
            group_id=gid,
            title=str(row.get("title") or ""),
            username=str(row.get("username") or ""),
            missing=missing,
            link_needed=bool(missing) and not public and not link,
        ))
    return plans


def _accounts_order(plans: Sequence[JoinPlan]) -> list[str]:
    seen: list[str] = []
    for plan in plans:
        for account in plan.missing:
            if account not in seen:
                seen.append(account)
    return sorted(seen)


def stagger_joins(plans: Sequence[JoinPlan], limit_per_account: int) -> list[tuple[str, JoinPlan]]:
    """Порядок вступлений «по кругу» с лимитом на аккаунт за запуск.

    Так 11 аккаунтов не вваливаются в 30 групп одновременно: каждый берёт
    по одной группе из круга, пока не кончится свой лимит.
    """
    accounts = _accounts_order(plans)
    queues = [[p for p in plans if account in p.missing and not p.link_needed]
              for account in accounts]
    counters = {account: 0 for account in accounts}
    out: list[tuple[str, JoinPlan]] = []
    changed = True
    while changed:
        changed = False
        for account, queue in zip(accounts, queues):
            if counters[account] >= limit_per_account or not queue:
                continue
            plan = queue.pop(0)
            counters[account] += 1
            out.append((account, plan))
            changed = True
    return out


def _group_rows(buttons: Iterable[tuple[int, int, str]]) -> list[list[tuple[int, int, str]]]:
    rows: dict[int, list[tuple[int, int, str]]] = {}
    for item in buttons:
        rows.setdefault(item[0], []).append(item)
    return [sorted(rows[key], key=lambda b: b[1]) for key in sorted(rows)]


def botgate_candidates(buttons: Iterable[tuple[int, int, str]]) -> list[ButtonCandidate]:
    """Безопасные кнопки проверки из rows/cols инлайн-клавиатуры.

    Возвращает строго те, что совпали с белым списком и не попали в чёрный.
    Ссылочные кнопки (урлы) сюда не попадают: их нажатие уводит из чата.
    """
    out: list[ButtonCandidate] = []
    for row_index, row in enumerate(_group_rows(buttons)):
        for col_index, (_row_id, _col_id, text) in enumerate(row):
            if GATE_BUTTON_BLACKLIST.search(text):
                continue
            if GATE_BUTTON_WHITELIST.search(text):
                out.append(ButtonCandidate(row_index, col_index, text.strip()))
    return out


def is_gate_message(text: str) -> bool:
    """Похоже ли сообщение на требование проверки (для «только сообщать»)."""
    sample = (text or "").lower()
    return bool(re.search(r"(проверк|капч|captcha|я не бот|verify|подтверд)", sample))


def plan_conduct(
    groups: Sequence[Mapping[str, object]],
    people: Sequence[Mapping[str, object]],
    players: Mapping[str, Mapping[str, object]],
    edges: Mapping[str, Iterable[int]],
) -> dict:
    """Плоский план: каждая группа и каждый человек — одному аккаунту.

    groups/people: цели; players: account -> {budget, used}; edges:
    account -> set(group_id), где он состоит. Группы обрабатываются первыми
    (эксклюзив — владельцу, общие — у кого больше свободного бюджета), затем
    люди (по кругу по свободным бюджетам).
    """
    free = {}
    for account, player in players.items():
        cap = int(player.get("budget") or 0)
        left = max(0, cap - int(player.get("used") or 0))
        if left:
            free[account] = left
    planned = {account: 0 for account in free}

    def take(candidates: Iterable[str]) -> str | None:
        eligible = [n for n in candidates if free.get(n, 0) > 0]
        if not eligible:
            return None
        who = min(eligible, key=lambda n: (-free[n], planned[n], n))
        free[who] -= 1
        planned[who] += 1
        return who

    out_groups: list[dict] = []
    out_people: list[dict] = []
    for row in groups:
        gid = int(row["id"])  # type: ignore[arg-type]
        holders = [n for n in free if gid in set(edges.get(n, ()))]
        who = take(holders)
        out_groups.append({"target": gid, "account": who,
                           "reason": "нет права писать" if who is None else ""})
    account_order = sorted(free, key=lambda n: (-free[n], n))
    cursor = 0
    for person in people:
        who = None
        for _ in range(max(len(account_order), 1)):
            if not account_order:
                break
            candidate = account_order[cursor % len(account_order)]
            cursor += 1
            if free.get(candidate, 0) > 0:
                who = take([candidate])
                break
        out_people.append({"target": person.get("user_id") or person.get("id"),
                           "account": who,
                           "reason": "бюджеты исчерпаны" if who is None else ""})
    return {"groups": out_groups, "people": out_people,
            "left": dict(free), "planned": planned}


def coverage_matrix(
    groups: Sequence[Mapping[str, object]],
    edges: Mapping[str, Iterable[int]],
    accounts: Sequence[str],
) -> dict:
    """Матрица «группа × аккаунт» + сводка по аккаунтам и группам."""
    rows = []
    per_account = {account: 0 for account in accounts}
    for group in groups:
        gid = int(group["id"])  # type: ignore[arg-type]
        marks = {account: gid in set(edges.get(account, ())) for account in accounts}
        for account, inside in marks.items():
            per_account[account] += int(inside)
        rows.append({"id": gid, "title": str(group.get("title") or ""), "marks": marks})
    full = [row["id"] for row in rows if all(row["marks"].values())]
    return {"rows": rows, "per_account": per_account,
            "full_groups": full, "groups": len(rows), "accounts": list(accounts)}
