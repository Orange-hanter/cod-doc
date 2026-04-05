# 📦 Спецификация модулей: integration-test

> 📊 Meta: `{"version": "0.1", "status": "DRAFT", "created": "2026-04-05", "author": "COD-DOC Orchestrator"}`

---

## Модули и их контракты

### `module/api` — Presentation Layer

**Ответственность:** Принимает внешние запросы, валидирует, делегирует в Application Layer.

| Компонент | Тип | Интерфейс |
|---|---|---|
| `Router` | Синглтон | `register(method, path, handler)` |
| `Controller` | Class | `handle(req: Request): Response` |
| `Middleware` | Function | `(req, res, next) => void` |
| `Serializer` | Utility | `serialize<T>(data: T): JSON` |

**Входные данные:** HTTP Request (headers, body, query params)  
**Выходные данные:** HTTP Response (status code, JSON body)  
**Ошибки:** `400 Bad Request`, `401 Unauthorized`, `403 Forbidden`, `500 Internal Server Error`

---

### `module/app` — Application Layer

**Ответственность:** Реализует бизнес-сценарии (Use Cases), не содержит бизнес-правил.

| Компонент | Тип | Интерфейс |
|---|---|---|
| `UseCase<I,O>` | Interface | `execute(input: I): Promise<O>` |
| `CommandHandler<C>` | Abstract | `handle(command: C): Promise<void>` |
| `QueryHandler<Q,R>` | Abstract | `handle(query: Q): Promise<R>` |
| `EventBus` | Singleton | `publish(event: DomainEvent): void` |

**Зависимости:** Инжектируются через конструктор (DI Container).  
**Транзакции:** Управляются на уровне UseCase через `UnitOfWork`.

---

### `module/domain` — Domain Layer

**Ответственность:** Бизнес-правила, инварианты, доменные события. Нет зависимостей.

| Компонент | Тип | Интерфейс |
|---|---|---|
| `Entity` | Abstract Class | `id: EntityId`, `equals(other): boolean` |
| `ValueObject<T>` | Abstract Class | `value: T`, `equals(other): boolean` |
| `Aggregate` | Abstract Class | `domainEvents: DomainEvent[]`, `clearEvents()` |
| `Repository<T>` | Interface | `findById`, `findAll`, `save`, `delete` |
| `DomainService` | Class | Зависит от домена |

**Правило:** Domain Layer не импортирует ничего из `module/api`, `module/app`, `module/infra`.

---

### `module/infra` — Infrastructure Layer

**Ответственность:** Техническая реализация: БД, кэш, очереди, внешние API.

| Компонент | Тип | Реализует |
|---|---|---|
| `RepositoryImpl` | Class | `Repository<T>` из Domain |
| `MessageBroker` | Class | `publish(topic, message)`, `subscribe(topic, handler)` |
| `CacheAdapter` | Class | `get(key)`, `set(key, value, ttl)`, `del(key)` |
| `ExternalAPIClient` | Class | `request(method, url, body)` |

**Конфигурация:** Все параметры подключений берутся из переменных окружения.

---

## Схема зависимостей

```
api  →  app  →  domain  ←  infra
         ↑________________________|
         (infra реализует интерфейсы domain,
          app использует через DI)
```

---

*Файл обновлён: 2026-04-05 | Автор: COD-DOC Orchestrator*
