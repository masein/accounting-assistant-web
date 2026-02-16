# Entity retrieval and linking

## How it works today

1. **Extraction (who to link)**  
   When the AI returns a transaction it may include `entity_mentions: [{ role, name }, ...]`. If it doesn’t, the backend infers mentions from text using **regex rules** (e.g. known bank names, “paid X”, “from X”). That inference is **not general**: new banks or phrasing need code changes.

2. **Resolution (name → entity in DB)**  
   For each `(role, name)` the backend calls **DB**: find entity by type + name (case-insensitive); if missing, **create** and return. So “retrieving” is **DB-first**: we always resolve by name in the database, never by an external API or by AI. The only non-DB part is **extraction** (deciding which names and roles from the text).

3. **Frontend**  
   After a suggestion, the frontend loads all entities (`GET /entities`), then for each mention finds an option by **name match** (case-insensitive) and sets the dropdown. When saving the voucher it sends `entity_links: [{ role, entity_id }]` (or `name` for get-or-create).

## Limitations

- **Extraction** is regex/heuristic: good for “Melli”, “paid Ali Roshan”, “from Innotech”; bad for new banks, other languages, or odd phrasing.
- **Matching** is exact name only (after normalization). No fuzzy search, no “did you mean?”.
- **No per-entity API**: we don’t call an external API per mention; we only use the app DB.

## Best practice (what we do)

- **Use the DB as the single source of truth.** Resolve every mention to an entity by (type, name) in the DB; create if not found. Do not use AI to “guess” an entity id.
- **Extraction** can stay AI + regex for now. For a more general solution you could (a) prompt the AI to always return `entity_mentions`, or (b) use a small NER/model call only for extraction, then always resolve via DB.
- **Optional: search API** so UIs can list/filter entities (e.g. `GET /entities?search=Mel&type=bank`) instead of loading the full list.
- **Optional: resolve API** so any client can send `[{ role, name }]` and get back `[{ role, name, entity_id }]` (get-or-create in one call). The chat flow can use the same logic and return `entity_id` so the frontend doesn’t depend on name matching.

## APIs added

- **GET /entities?type=&search=**  
  List entities; `type` filters by entity type; `search` filters by name (substring, case-insensitive). Use for typeahead or “search entities” UIs.

- **POST /entities/resolve**  
  Body: `{ "mentions": [ { "role": "bank", "name": "Melli" }, ... ] }`.  
  Response: `{ "resolved": [ { "role", "name", "entity_id" } ] }`.  
  Each mention is resolved with get-or-create (find by type + name, or create). Use this to turn free-text mentions into entity ids in one call.

- **Chat response**  
  When the backend has `entity_mentions` it now also returns **resolved** entity ids (same get-or-create). The frontend can set dropdowns by `entity_id` when present, and only fall back to name matching when needed.
