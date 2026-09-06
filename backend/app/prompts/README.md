# Prompt conventions

- One role/system prompt per LLM agent: `insight_system.md`, `knowledge_system.md`,
  `reporting_system.md` (§14). No inline prompt strings in application code.
- Loaded via `app/llm/client.py:load_prompt(name, **vars)`; cached; `{{placeholder}}`
  interpolation for run-specific values.
- All LLM calls use JSON mode; outputs are pydantic-validated (`app/llm/schemas.py`)
  before touching the DB. Invalid JSON → one corrective re-prompt, then failure.
- Hard rules live in the prompts AND in code (checkpoints, QA verification) —
  prompts are guidance, code is enforcement.
- `schema_mapping_system.md` is a P2 future extension (deterministic alias path is default).
