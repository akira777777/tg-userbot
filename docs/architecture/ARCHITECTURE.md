# Архитектура

> Архитектурный обзор tg-userbot.

## Высокоуровневая архитектура

```
┌─────────────────────────────────────────┐
│  Client Layer (Telegram, HTTP, Web UI)   │
├─────────────────────────────────────────┤
│  Application Layer                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐
│  │ Handlers │  │  API     │  │  Media    │
│  │          │  │  Routes  │  │  Library  │
│  └──────────┘  └──────────┘  └──────────┘
├─────────────────────────────────────────┤
│  Domain Layer (бизнес-логика)           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐
│  │  Search  │  │  Upload  │  │  Media    │
│  │          │  │  Download│  │  Metadata │
│  └──────────┘  └──────────┘  └──────────┘
├─────────────────────────────────────────┤
│  Repository Layer                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐
│  │  Supabase│  │  Redis   │  │  Local    │
│  │  DB      │  │  Cache   │  │  Storage  │
│  └──────────┘  └──────────┘  └──────────┘
└─────────────────────────────────────────┘
```

## Слои архитектуры

### Client Layer

- **Telegram Bot API** — обработка сообщений, медиа, файлов
- **HTTP API** — RESTful API (FastAPI)
- **Web UI** — React-панель

### Application Layer

- **Handlers** — обработчики команд
- **API Routes** — маршруты HTTP API
- **Media Library** — медиа-библиотека

### Domain Layer

- **Search** — поиск по медиатеке
- **Upload** — загрузка файлов
- **Download** — скачивание файлов
- **Metadata** — метаданные (артист, альбом, жанр)

### Repository Layer

- **Supabase DB** — PostgreSQL (хранение данных)
- **Redis** — кеширование, rate-limiting
- **Local Storage** — локальное хранилище файлов

## Паттерны проектирования

### Repository Pattern

```python
class MediaRepository(Repository):
    def find_by_id(self, media_id: int) -> Media | None: ...
    def find_by_query(self, query: str) -> list[Media]: ...
    def find_by_tags(self, tags: list[str]) -> list[Media]: ...
    def find_by_category(self, category: str) -> list[Media]: ...
```

### Service Layer

```python
class MediaService(Service):
    def upload(self, file: File, metadata: Metadata) -> Media: ...
    def search(self, query: str) -> list[Media]: ...
    def download(self, media_id: int) -> bytes: ...
```

### DTO Pattern

```python
class MediaCreateDTO(BaseModel):
    title: str
    url: str
    category: str
    tags: list[str]
```

### Event Sourcing

```python
class EventStore:
    def append(self, event: Event) -> EventId: ...
    def replay(self) -> list[Event]: ...
```

## Базы данных

### PostgreSQL (Supabase)

- **users** — пользователи
- **media** — медиа-файлы
- **collections** — коллекции
- **tags** — теги
- **metadata** — метаданные
- **views** — просмотры

### Redis

- **Кеширование** — результаты поиска, мета-данные
- **Rate limiting** — ограничение частоты запросов
- **Pub/Sub** — уведомления о новых медиа

### Local Storage

- **Media files** — файлы в S3/MinIO
- **Thumbnails** — миниатюры
- **Index** — векторное представление

## Кеширование

```
media (медиа-файлы) ──┐
                        │
metadata (мета-данные)─┼──> Redis (TTL: 1ч)
                        │
search (результаты)    ─┘
```

## Мониторинг

### Prometheus

- **media.added** — новое медиа
- **media.searched** — поиск
- **media.failed** — ошибки
- **media.views** — просмотры

### Grafana

- Дашборды:
  - Медиа-библиотека
  - Поиск
  - Загрузка/скачивание
  - Ошибки

## Логи

```
Format: JSON
Fields: timestamp, level, module, event, data
```

## Тестирование

- **Unit tests** — модульная логика
- **Integration tests** — взаимодействие с БД, Redis
- **End-to-end tests** — полный стек (Docker Compose)

## Производительность

- **Поиск**: 0.1–2 мс (векторный поиск Chroma)
- **Пагинация**: cursor-based (для больших датасетов)
- **Кеширование**: Redis (TTL: 1 час)
- **Индексы**: SQLite (малые датасеты), PostgreSQL (большие)

## Безопасность

- **JWT** — аутентификация
- **Rate limiting** — ограничение частоты запросов
- **Миграция**: шифрование файлов на диске
- **Сквозное шифрование** — S3/MinIO

## Масштабируемость

- **Горизонтальное масштабирование** — stateless сервисы (N экземпляров)
- **Вертикальное масштабирование** — добавление CPU/RAM
- **Шардирование** — разделение данных по хостам
- **Автоскейлинг** — Kubernetes + HPA

## Вклад в архитектуру

- Пиши архитектурные решения
- Добавляй диаграммы
- Улучшай документацию

## Вклад в код

- Пиши модульный код
- Используй type hints
- Пиши docstring

## Вклад в тесты

- Пиши unit-тесты
- Пиши интеграционные тесты
- Не снижай coverage ниже 80%

## Вклад в документацию

- Пиши гайды
- Добавляй примеры
- Исправляй ошибки

## Вклад в инфраструктуру

- Docker Compose
- CI/CD
- Мониторинг

## Вклад в дизайн

- UI/UX
- Иконки
- Шрифты

## Вклад в маркетинг

- Статья в блог
- Видео-туториал
- Пост в соцсети

## Вклад в поддержку

- Ответы на вопросы
- Гайды
- Участие в разработке

## Вклад в баг-репорты

- Issue с минимальным воспроизведением
- Версия, ОС, шаги воспроизведения
- Скриншоты

## Вклад в оптимизацию

- Профилирование
- Оптимизация hot path
- Кеширование

## Вклад в безопасность

- Аудит кода
- Типы в сигнатурах
- Исправление уязвимостей

## Вклад в производительность

- Оптимизация запросов к БД
- Индексы
- Оптимизация поиска

## Вклад в DevEx

- Улучшение DX
- Улучшение UX
- Команды в Makefile

## Вклад в локализацию

- Добавление строк в `i18n/`
- Перевод README + docs
- Исправление грамматики

## Вопросы?

Создай issue или напиши в `#architecture` в Discord.

## Лицензия

MIT © Novra
