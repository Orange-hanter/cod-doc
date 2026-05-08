## 📝 Diff Summary
<!-- Краткое описание изменений (1–3 предложения) -->


## 🎯 Зачем
<!-- Мотивация: ссылка на task (PCA-XXX / COD-XXX), RFC (proposals/NN-…), или бизнес-причина -->


## 🧪 Как проверить
<!-- Шаги для ревьюера: команды, ожидаемый вывод -->


## ⚠️ Риски
<!-- Что может пойти не так? Что покрыто тестами? -->


## 🤖 Model used
<!-- AI-модель / автор. Например: claude-sonnet-4-6 | claude-opus-4-7 | human-authored | gpt-... -->


## 🔍 Validation Block
- [ ] Хэши ссылок сверены (`python tools/hash_calc.py update MASTER.md`)
- [ ] Self-check JSON прикреплён (см. ниже)
- [ ] Changelog в `MASTER.md` обновлён
- [ ] Нет выдуманных артефактов — все ссылки указывают на реальные файлы
- [ ] Статусы разделов актуальны (`🟡 DRAFT` / `🟢 VERIFIED` / `🔴 STALE`)

## 🧩 Затронутые разделы
<!-- Перечислите изменённые разделы MASTER.md -->
- 

## ✅ Definition of Done
<!-- См. AGENTS.md §11 -->
- [ ] Поведение соответствует acceptance criterion'у задачи или RFC
- [ ] `ruff`, `mypy`, `pytest` зелёные локально
- [ ] Контракты синхронизированы (модель ↔ migration ↔ MCP ↔ docs)
- [ ] Если изменение видимо в UI — приложен скриншот / описание
- [ ] Activity events эмитятся при write-операциях (если новый MCP-write-tool)
- [ ] Закрытие задачи в БД через `task_complete` или `task_update_status`
- [ ] Если закрыта секция плана — audit-report в `docs/system/audit/`

## 📎 Self-Check JSON
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

## 💬 Инструкции для ревьюера
- `✅ APPROVE` → мерж в `main`, автоматически запускается `post-merge` хук
- `⚠️ REQUEST CHANGES` → агент парсит комментарии, вносит правки в эту же ветку
