# 📦 Module Specification: integration-test

> 📊 Meta: `{"version": "0.1", "status": "DRAFT", "created": "2026-04-05", "author": "COD-DOC Orchestrator"}`

---

## Modules and their contracts

### `module/api` — Presentation Layer

**Responsibility:** Accept external requests, validate, delegate to the Application Layer.

| Component | Type | Interface |
|---|---|---|
| `Router` | Singleton | `register(method, path, handler)` |
| `Controller` | Class | `handle(req: Request): Response` |
| `Middleware` | Function | `(req, res, next) => void` |
| `Serializer` | Utility | `serialize<T>(data: T): JSON` |

**Input:** HTTP Request (headers, body, query params)  
**Output:** HTTP Response (status code, JSON body)  
**Errors:** `400 Bad Request`, `401 Unauthorized`, `403 Forbidden`, `500 Internal Server Error`

---

### `module/app` — Application Layer

**Responsibility:** Implements business scenarios (Use Cases), contains no business rules.

| Component | Type | Interface |
|---|---|---|
| `UseCase<I,O>` | Interface | `execute(input: I): Promise<O>` |
| `CommandHandler<C>` | Abstract | `handle(command: C): Promise<void>` |
| `QueryHandler<Q,R>` | Abstract | `handle(query: Q): Promise<R>` |
| `EventBus` | Singleton | `publish(event: DomainEvent): void` |

**Dependencies:** Injected via the constructor (DI Container).  
**Transactions:** Managed at the UseCase level via `UnitOfWork`.

---

### `module/domain` — Domain Layer

**Responsibility:** Business rules, invariants, domain events. No dependencies.

| Component | Type | Interface |
|---|---|---|
| `Entity` | Abstract Class | `id: EntityId`, `equals(other): boolean` |
| `ValueObject<T>` | Abstract Class | `value: T`, `equals(other): boolean` |
| `Aggregate` | Abstract Class | `domainEvents: DomainEvent[]`, `clearEvents()` |
| `Repository<T>` | Interface | `findById`, `findAll`, `save`, `delete` |
| `DomainService` | Class | Depends on the domain |

**Rule:** The Domain Layer does not import anything from `module/api`, `module/app`, `module/infra`.

---

### `module/infra` — Infrastructure Layer

**Responsibility:** Technical implementation: DB, cache, queues, external APIs.

| Component | Type | Implements |
|---|---|---|
| `RepositoryImpl` | Class | `Repository<T>` from Domain |
| `MessageBroker` | Class | `publish(topic, message)`, `subscribe(topic, handler)` |
| `CacheAdapter` | Class | `get(key)`, `set(key, value, ttl)`, `del(key)` |
| `ExternalAPIClient` | Class | `request(method, url, body)` |

**Configuration:** All connection parameters come from environment variables.

---

## Dependency scheme

```
api  →  app  →  domain  ←  infra
         ↑________________________|
         (infra implements domain interfaces,
          app uses them via DI)
```

---

*File updated: 2026-04-05 | Author: COD-DOC Orchestrator*
