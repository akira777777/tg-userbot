# Прага: где размещать рекламу — карта каналов и чатов

Составлено 19.11.2026. Все @username проверены по t.me и через живой
Telegram-поиск (contacts.search), дальше — влиты в базу `tg-userbot`.

## Как это работает теперь

```bat
:: найти новые группы по гео «Прага» (16 поисковых фраз -> ~130 групп)
python bot.py discover --dry-run
:: темы: rent | jobs | market | news
python bot.py discover --theme rent --dry-run
:: с проверкой прав записи (каждая группа = один запрос)
python bot.py discover --verify --export exports/prague_rent.csv
:: влить отобранное в базу как рабочий список
python bot.py discover --theme market
```

Найденное кладётся в таблицу `groups` с пометкой `discovered_at`; у групп,
где писать нельзя (каналы, закрытый постинг), `in_broadcast=0` и `skip_reason`.

## Что нашлось (живая цифры на 19.11.2026)

**Крупнейшие городские площадки**
- @PrahaChatik — Прага чатик | CHATIK, ~20 100 участников, реклама через @CHATIK_Ad
- @prahalivecz — Українці в Чехії | Прага, ~11 500, чат с открытым постингом
- @czech_granitsa — Чехия Прага Чат (OFF MAP), ~4 380, мигранты: ВНЖ, жильё, работа
- @czech_info_chat4 — Городской чат | Прага, ~4 480, реклама по согласованию с админом
- @buddiesprague — Друзья в Праге, ~4 570, свободное общение
- @ok_chat_czech ~6 590, @czechia_2020 ~5 520, @ChatPraha ~1 750, @thecesko_chat ~1 450

**Новости и жизнь Праги (крупные каналы, но постинг только платный)**
- @pozor_brno — Pozor! Чехия, ~54 500 (реклама @pozor_support)
- @vinegret_cz — Vinegret.cz, ~42 100 (реклама @vinegret_kontakty)
- @the_cesko — The Česko, ~36 500 (реклама @beru_media)
- @czech_info — Новости | Прага, ~26 700; @czech_ru ~8 400; @pragatoday ~10 200

**Работа (самое сильное по объёму аудитории)**
- @prage_vakansii — Прага Работа | Подработка, ~110 000
- @Praha_Robota_CZ — Українці в Чехії | Прага Робота, ~17 700
- @WorkJobCZ ~16 800, @Poiskprace ~15 200, @findjob_cz ~14 300, @awacz ~12 800
- @rabotachehiya ~11 900, @work_praha ~10 000, @studentsprague ~8 400
- @Vova5621 — Чехия-Прага Работа-Фушки, ~28 000

**Аренда жилья / соседи**
- @arendakomnatpraha ~14 500, @sosedi_prague ~12 050, @arendapragaua ~9 800
- @cz_bydleni — Жилье, соседи | Прага, ~9 100, @localno_praha ~8 300, @arenda_prague ~7 300
- @ArendaPraga ~5 460, @GoldKeyCesko ~4 050

**Барахолка / объявления (лучший вход для коммерческого поста)**
- @Vova5621, @BazarPrague ~8 100, @BuddiesPrague, @praga_obyavleniya ~5 800
- @Baracholka_Praha ~4 550, @BaraxolkaPeague ~4 140, @Prague_HELLO ~6 850
- @baraholkaprague — Pozor! Барахолка, ~13 800 (объявления через бот @inzerko_bot)

**Студенты и адаптация**
- @studentsprague ~8 400, @student_praga ~10 300, @kdovi_chat ~9 600 (Вопросник),
  @czu_cz ~1 700, @comp_cz — Чат айтишников | Прага, ~1 500, @women_cz ~1 330

**Афиша/события (для промо мероприятий)**
- @pragafisha ~16 400, @afishacz ~8 700

## Правила, без которых это не работает

1. **Сначала `groups --probe`, потом `broadcast`**: канал или чат с закрытым
   постингом — не площадка для поста, а только для платной рекламы через админа.
2. **Каждый аккаунт вступает сам** (`bot.py join`): писать в группу, где аккаунт
   не состоит, нельзя; вступление свежему аккаунту — не больше 5–10 в день.
3. **Крупные каналы — платно и законно**: у всех из списка новостей есть
   контакт для рекламы, писать в них от своего имени без договорённости =
   гарантированный бан аккаунта и жалоба.
4. **Сначала 3–5 площадок**, замерить отклик (`bot.py status`), потом расширять.
