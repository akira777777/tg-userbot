# tg-userbot

Инструмент на Telethon для личного Telegram-аккаунта (userbot):

1. **Сбор никнеймов участников** групп → SQLite (`members`), с указанием, из какой группы кто.
2. **Авто-добавление групп в список рассылки** — `groups --sync` (разово) и `watch` (дежурный режим, новые группы добавляются при первом сообщении из них).
3. **Отправка сообщений** — два режима:
   * `broadcast` — одно сообщение в каждую группу из списка рассылки;
   * `dmsend` — личные сообщения собранным участникам, с троттлингом,
     дедупликацией по прошлым кампаниям и чёрным списком.
4. `export` — выгрузка SQLite → CSV (Excel-совместимо, UTF-8 BOM).

Практическое руководство: [РУКОВОДСТВО.md](РУКОВОДСТВО.md). Короткая ссылка: [docs/GUIDE.md](docs/GUIDE.md).

## Как запускать на этой машине

Интерпретатор: `.venv\Scripts\python.exe`. После правки очереди DM перезапустите `web.py`, иначе панель покажет старый `dm_available`.

| Что | Команда |
| --- | --- |
| Сводка по базе | `.\.venv\Scripts\python.exe bot.py status` |
| Панель | `.\.venv\Scripts\python.exe web.py` |
| Тесты без Telegram | `.\.venv\Scripts\python.exe -m pytest` |
| Тесты дашборда | `npm --prefix dashboard test` |
| Docker панели | `docker compose -f docker-compose.yml up web` |

`make status` / `make test` / `make web` зовут те же команды. Пакета `src.tg_userbot` нет. `docker compose up` без файла — это [docker-compose.yml](docker-compose.yml) с панелью и SQLite, не FastAPI и не Postgres. Mini App: [docker-compose.prod.yml](docker-compose.prod.yml) только с реальными `MINI_APP_URL` / `OWNER_TELEGRAM_IDS` / `BOT_TOKEN`.

## Настройка с нуля

> Готово на этой машине: `.env` заполнен, сессия `userbot.session` авторизована
> (ник аккаунта — вне git, смотрите `bot.py status`), интерпретатор — `.venv/Scripts/python.exe`.
> Запускать так: `cd <каталог проекта> && .venv\Scripts\python.exe bot.py <команда>`

1. Зайдите на **https://my.telegram.org** → авторизация номером телефона →
   *API development tools* → создайте приложение (название/описание — любые) →
   скопируйте `api_id` и `api_hash`.
2. Установите зависимости (Telethon уже должен быть):
   ```bash
   pip install -r requirements.txt
   ```
3. `copy .env.example .env` — впишите `API_ID` и `API_HASH`.
4. Авторизация сессии (придёт код из Telegram-сервиса, введите его в консоль):
   ```bash
   python bot.py login
   ```
   Появится файл `userbot.session` — не теряйте и не передавайте его.

## Команды

```bash
python bot.py groups --sync          # добавить все мои группы/каналы в список рассылки
python bot.py groups --list          # посмотреть список
python bot.py watch                  # дежурить: новые группы добавляются сами,
                                     # ответ «стоп» в DM заносит в blacklist

python bot.py scrape                 # собрать ники участников всех групп из списка
python bot.py scrape --groups 123 456
# Ограничение Telegram: полный список участников (GetParticipants) доступен
# только админам каналов/мегагрупп; в остальных scrape пишет
# «список участников закрыт» и переходит дальше.

python bot.py broadcast --text "Привет, всем!" --dry-run   # предпросмотр
python bot.py broadcast --text "Привет, всем!"             # рассылка по группам

python bot.py dmsend --file msg.txt --group 123 --limit 10 --dry-run
python bot.py dmsend --file msg.txt                        # DM по собранным никам

python bot.py status                 # сводка: группы, ники, кампании, кто ещё не обписан
python bot.py export                 # exports/groups.csv, exports/members.csv, exports/blacklist.csv
python bot.py blacklist add 123456   # руками занести в ЧС; blacklist list — посмотреть
```

## Про DM — прочитайте перед запуском

Массовые личные сообщения незнакомым людям — это **спам по ToS Telegram**;
аккаунты за это ограничивают (`FloodWait` → temporary ban → полный бан).
Инструмент по умолчанию старается минимизировать риск, но решение о
целесообразности на вас:

* запуск `dmsend` — максимум `--limit 20` получателей, паузы 20–45 с (рандомные);
* кому уже писали в прошлых DM-кампаниях — повторно не шлётся (флаг `--repeat-ok` отменяет);
* боты, удалённые аккаунты, строки без ника и без `access_hash`, постоянные отказы
  и `blacklist` исключаются автоматически; человек, ответивший «стоп» в
  DM в режиме `watch`, заносится в `blacklist` сам;
* канал, где аккаунт только подписчик, и чат без права писать в рассылку не попадают;
* `FloodWait` ставит аккаунт на паузу на время, которое прислал Telegram; эта цель остаётся ему, другой аккаунт её не забирает. `PeerFlood` — пауза на 24 часа, и очередь DM другим аккаунтам не передаётся;
* **рекомендации:** сначала 5–10 сообщений и пауза день-два; не слать незнакомым —
  только тем, кто состоит в ваших группах по интересам и ждёт от вас вестей;
  текст должен идентифицировать вас и содержать фразу «ответь СТОП, чтобы я не писал».

Для по-настоящему массовых рассылок правильный инструмент — **бот в Telegram**
через официальную рассылку пользователям (Bot API + `sendMessage` по подписной
базе), а не userbot: там лимиты прозрачные и аккаунт не жалко.

## Хранение

`userbot.db` (SQLite): `groups`, `members`, `member_sources`, `campaigns`,
`sent` (журнал статусов каждой попытки), `blacklist`, `tb_*` (облачное зеркало),
`media` (библиотека через «Избранное»). Экспорт — `exports/*.csv`.

## Облачное зеркало (Supabase) + дашборд

Собранные данные синхронизируются в Supabase (таблицы `tb_*`), что даёт:
- наблюдение со любого устройства (дашборд на Vercel);
- бэкап агрегатов;
- Realtime-подписки на изменения.

### Дашборд

- URL: `https://tg-userbot-dashboard-b1ujm4kbi-akira77777777.vercel.app`
- Вход: введите `DASH_TOKEN` (из `.env`, не в репозитории).
- Автообновление: каждые 15 с.

### Деплой дашборда

```bash
cd dashboard
vercel deploy -y           # preview
vercel deploy --prod -y    # production
```

### Схемы

- SQL: `supabase/schema.sql` (применить в Supabase → SQL Editor).
- Авто-push: `AUTO_CLOUD=1` в `.env` — после broadcast/dmsend/conduct/
  scrape/probe/privacy/align/accounts/media/blacklist/export зеркало обновляется.
