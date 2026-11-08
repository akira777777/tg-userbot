# API Reference

> OpenAPI 3.0 specification для tg-userbot.

## База URL

```
http://localhost:8000/api
```

## Аутентификация

Все запросы требуют заголовок `Authorization: Bearer <jwt_token>`.

JWT-токен выдаётся при регистрации:

```http
POST /api/auth/register
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "secret",
  "name": "John"
}

# → { "token": "eyJhbGciOiJIUzI1NiIs...", "expires_in": 86400 }
```

## Медиа

### `/api/media` — CRUD

| Метод | Путь | Описание |
|-------|------|----------|
| GET   | `/api/media` | Список медиа-файлов (пагинация, сортировка, фильтрация) |
| GET   | `/api/media/{id}` | Получить медиа-файл |
| POST  | `/api/media` | Создать медиа-файл |
| PUT   | `/api/media/{id}` | Обновить медиа-файл |
| DELETE| `/api/media/{id}` | Удалить медиа-файл |

#### GET /api/media

Параметры:

| Параметр | Тип | Описание |
|----------|-----|----------|
| `page`   | int | Номер страницы (1-based) |
| `limit`  | int | Количество записей на страницу (1–100) |
| `q`      | str | Поиск по заголовку, описанию, тегам |
| `category` | str | Фильтр по категории |
| `tags`   | str | Фильтр по тегам (через запятую) |
| `sort`   | str | Сортировка (`created_at`, `views`, `size`, `duration`) |
| `order`  | str | Направление (`asc`, `desc`) |

Пример:

```http
GET /api/media?page=1&limit=20&q=lofi&category=music&sort=views&order=desc
```

Ответ:

```json
{
  "data": [
    {
      "id": 1,
      "title": "Lofi Beats #1",
      "url": "https://cdn.example.com/media/1.mp4",
      "thumbnail": "https://cdn.example.com/media/thumbs/1.jpg",
      "duration": 180,
      "size": 52428800,
      "category": "music",
      "tags": ["lofi", "beats"],
      "views": 1234,
      "created_at": "2024-11-08T10:00:00+00:00"
    }
  ],
  "meta": {
    "total": 1500,
    "page": 1,
    "per_page": 20,
    "total_pages": 75
  }
}
```

#### POST /api/media

Запрос:

```http
POST /api/media
Content-Type: multipart/form-data

file: <binary>
title: "Lofi Beats #1"
description: "Чилл-бит для работы и учёбы"
category: music
tags: lofi,beats,study
```

Ответ:

```json
{
  "id": 1,
  "title": "Lofi Beats #1",
  "url": "https://cdn.example.com/media/1.mp4",
  "thumbnail": "https://cdn.example.com/media/thumbs/1.jpg",
  "duration": 180,
  "size": 52428800,
  "category": "music",
  "tags": ["lofi", "beats"],
  "views": 0,
  "created_at": "2024-11-08T10:00:00+00:00"
}
```

#### GET /api/media/{id}

```http
GET /api/media/1
```

Ответ:

```json
{
  "id": 1,
  "title": "Lofi Beats #1",
  "url": "https://cdn.example.com/media/1.mp4",
  "thumbnail": "https://cdn.example.com/media/thumbs/1.jpg",
  "duration": 180,
  "size": 52428800,
  "category": "music",
  "tags": ["lofi", "beats"],
  "views": 1234,
  "created_at": "2024-11-08T10:00:00+00:00",
  "artist": "Chill Cow",
  "album": "LoFi Study",
  "year": 2024
}
```

## Поиск

### POST /api/media/search

Пример:

```http
POST /api/media/search
Content-Type: application/json

{
  "query": "lofi beats",
  "limit": 20,
  "offset": 0
}
```

Ответ:

```json
{
  "results": [
    { "id": 1, "title": "Lofi Beats #1", "score": 0.92 },
    { "id": 2, "title": "Lofi Beats #2", "score": 0.85 }
  ],
  "total": 142
}
```

## Метаданные

### POST /api/media/metadata

Пример:

```http
POST /api/media/metadata
Content-Type: application/json

{
  "media_id": 1,
  "artist": "Chill Cow",
  "album": "LoFi Study",
  "year": 2024,
  "genre": "lofi hip hop"
}
```

Ответ:

```json
{
  "media_id": 1,
  "artist": "Chill Cow",
  "album": "LoFi Study",
  "year": 2024,
  "genre": "lofi hip hop"
}
```

## Категории

### GET /api/categories

```http
GET /api/categories
```

Ответ:

```json
[
  { "name": "music", "count": 520 },
  { "name": "video", "count": 310 },
  { "name": "audio", "count": 480 }
]
```

## Теги

### GET /api/tags

```http
GET /api/tags
```

Ответ:

```json
[
  { "name": "lofi", "count": 156 },
  { "name": "beats", "count": 142 },
  { "name": "study", "count": 98 }
]
```

## Статистика

### GET /api/stats

```http
GET /api/stats
```

Ответ:

```json
{
  "total": 1500,
  "views_total": 123456,
  "categories": [
    { "name": "music", "count": 520 },
    { "name": "video", "count": 310 }
  ],
  "top_viewed": [
    { "id": 1, "title": "Lofi Beats #1", "views": 12345 },
    { "id": 2, "title": "Lofi Beats #2", "views": 11234 }
  ]
}
```

## Ошибки

| Код | Тип | Описание |
|-----|-----|----------|
| 400 | BadRequestError | Неверный запрос |
| 401 | UnauthorizedError | Отсутствует или невалидный токен |
| 404 | NotFoundError | Ресурс не найден |
| 409 | ConflictError | Конфликт (дубликат заголовка) |
| 422 | ValidationError | Ошибка валидации |
| 500 | InternalError | Внутренняя ошибка сервера |
