## 📝 Diff Summary
<!-- Краткое описание изменений (1–3 предложения) -->


## 🔍 Validation Block
- [ ] Хэши ссылок сверены (`python tools/hash_calc.py update MASTER.md`)
- [ ] Self-check JSON прикреплён (см. ниже)
- [ ] Changelog в `MASTER.md` обновлён
- [ ] Нет выдуманных артефактов — все ссылки указывают на реальные файлы
- [ ] Статусы разделов актуальны (`🟡 DRAFT` / `🟢 VERIFIED` / `🔴 STALE`)

## 🧩 Затронутые разделы
<!-- Перечислите изменённые разделы MASTER.md -->
- 

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
