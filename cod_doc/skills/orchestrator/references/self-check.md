# Reference — self_check блок

> Дозагружается при завершении любой задачи.

## 1. Обязательный формат

В конце выполнения задачи агент должен вывести JSON-блок:

```json
{
  "self_check": {
    "links_verified": true,
    "hashes_match": true,
    "no_hallucinations": true,
    "context_depth": "L1",
    "missing_info": []
  }
}
```

## 2. Поля

| Поле | Тип | Семантика |
|------|-----|-----------|
| `links_verified` | bool | Все упомянутые в результате ссылки прошли через `link_verify` или `check_stale_refs`. |
| `hashes_match` | bool | Хеши гибридных ссылок согласованы (через `verify_hash`). `false` допустимо при честном STALE-флаге. |
| `no_hallucinations` | bool | Содержимое опирается только на прочитанные доки и tool-результаты. `false` — explicitly mark, не commit. |
| `context_depth` | str | `L0` / `L1` / `L2` / `L3` — реально загруженный уровень (см. контракт `context_get`: L2 — цепочки зависимостей, L3 — плюс семантические совпадения). Не возвышай. |
| `missing_info` | list[str] | Список вещей, которых не хватало (для последующей задачи), либо `[]`. |

## 3. Когда `false`

- `links_verified=false` — допустимо, если задача не трогала ссылки.
- `hashes_match=false` — обязательно сопроводить explanation в `missing_info`.
- `no_hallucinations=false` — **не допустимо** для commit-ready задач.
  Если уверенности нет — переведи задачу в `in_review` с эскалацией.
- `context_depth` выше фактически загруженного — невалидно. `context_get`
  принимает L0|L1|L2|L3 (STB-013); L3 без эмбеддингов деградирует
  молча до L2 — пиши тот уровень, что реально вернулся, и отметь
  пропуск семантики в `missing_info`.

## 4. Связанное

- Эскалации FM-002/FM-003 — см. скилл [validation](../../validation/SKILL.md).
- Audit-cadence (закрытие фазы → audit-report; открытие → kickoff brief) —
  см. скилл [audit-cadence](../../audit-cadence/SKILL.md) (PCA-002).
