# CLAUDE.md — Jira DC-to-Cloud Migration Backend

## What this project is

A FastAPI backend that automatically translates Jira Data Center workflow scripts to Jira Cloud equivalents using AI. Built as a PFE (final year project) at **Spectrum Groupe**, a certified Atlassian partner.

The tool receives a JSON analysis report containing 12+ Jira DC components (ScriptRunner Groovy scripts, JSU rules, webhook handlers, native automation rules), routes each to a specialized AI translation agent, and returns a ZIP with translated Cloud-compatible scripts + a migration report.

## Tech stack

- **Backend**: Python 3.9+, FastAPI 0.135, Pydantic v2, Uvicorn
- **AI**: Google Gemini 2.5 Flash via `google-genai` SDK (direct prompt-per-agent, **no LangChain**)
- **RAG**: ChromaDB (local persistent) + Gemini `text-embedding-004` for doc retrieval
- **Scraping**: Playwright (headless Chromium) + BeautifulSoup for Atlassian doc scraping
- **Frontend**: React 18 + Vite 5 (separate repo, not here)
- **Config**: `.env` with `GEMINI_API_KEY`, loaded via `pydantic-settings`

## Architecture

```
POST /api/v1/migrate (or /migrate/stream for SSE)
  → AnalysisReport (list of Component objects)
  → Block Router (app/services/router.py) dispatches by component.plugin enum
  → One of 5 agents translates via Gemini:
      ScriptRunnerAgent  → .groovy    (DC Groovy → Cloud Groovy)
      JSUAgent           → .groovy    (JSU rules → ScriptRunner Cloud Groovy)
      WebhookAgent       → .py        (DC webhooks → Cloud Python handlers)
      AutomationAgent    → .json      (DC automation → Cloud Automation JSON)
      MiscAgent          → .groovy    (fallback: Groovy comment block with manual steps)
  → Validator checks for missing/failed translations
  → Packager creates ZIP: translated/ + flagged/ + migration_report.md
```

## File structure

```
main.py                          FastAPI app entry point
app/
  config.py                      Settings (gemini_api_key from .env)
  models.py                      Pydantic models: Component, AnalysisReport, enums
  agents/
    base_agent.py                BaseAgent: Gemini call, RAG injection, translate()
    scriptrunner.py              ScriptRunnerAgent (Groovy DC→Cloud)
    jsu.py                       JSUAgent (JSU→ScriptRunner Cloud)
    webhook.py                   WebhookAgent (DC webhook→Python Cloud)
    automation.py                AutomationAgent (DC→Cloud Automation JSON)
    misc.py                      MiscAgent (fallback/unsupported)
  services/
    router.py                    Block router: AGENT_MAP dispatch + streaming generator
    validator.py                 Result validation (missing code, failed translations)
    packager.py                  ZIP creation with translated files + report
  routers/
    migration.py                 API routes: /migrate, /migrate/stream, /download, /health
rag/
  __init__.py                    get_retriever() with graceful fallback
  chroma_client.py               Singleton PersistentClient, collection: jira_migration_docs
  embedder.py                    GeminiEmbedder wrapping text-embedding-004
  retriever.py                   Retriever.query(text, n_results=5) → [{content, source, score}]
scripts/
  index_docs.py                  CLI: chunk .md/.txt/.html files → embed → upsert to ChromaDB
  scrape_docs.py                 CLI: scrape 20 Atlassian/Adaptavist/Appfire doc URLs → docs/
docs/                            Scraped documentation (Markdown files with YAML frontmatter)
chroma_db/                       ChromaDB persistent storage (created at runtime)
app/outputs/                     Generated migration ZIP files
```

## Key patterns to preserve

- **Template method in BaseAgent**: All agents override `build_prompt()` only. `translate()` and RAG injection live in the base class — never duplicate this logic in subclasses.
- **RAG is optional**: `_inject_rag_context()` uses lazy import + try/except. If ChromaDB is missing, empty, or broken, agents silently fall back to prompts without context. Never make RAG a hard dependency.
- **RAG injection marker**: Context is inserted just before `## Original script` in every agent's prompt. All 5 agents use this heading — don't rename it.
- **SSE streaming**: `route_components_stream()` yields `agent_start` / `agent_done` events. The React frontend consumes these via `ReadableStream`. Don't change the event shape.
- **No LangChain**: This is a hard constraint. Embeddings use `google-genai` directly. Chunking is plain Python. ChromaDB is used with raw `collection.query()`.

## Commands

```bash
# Run the server
uvicorn main:app --reload

# Scrape documentation (one-time, requires: pip install playwright beautifulsoup4 && playwright install chromium)
python scripts/scrape_docs.py

# Index docs into ChromaDB (one-time, re-run when docs change)
python scripts/index_docs.py ./docs

# Verify ChromaDB collection
python -c "from rag import get_chroma_collection; c = get_chroma_collection(); print(f'{c.count()} docs')"
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Health check |
| GET | `/api/v1/health` | Health check |
| POST | `/api/v1/migrate` | Synchronous: process all components, return ZIP |
| POST | `/api/v1/migrate/stream` | SSE stream: per-component progress events, then ZIP path |
| GET | `/api/v1/download?path=...` | Download a generated ZIP file |

## Data model (input)

```json
{
  "analysis_id": "test-001",
  "source_environment": "Jira DC 9.x",
  "target_environment": "Jira Cloud",
  "analysis_date": "2026-03-30",
  "components": [
    {
      "component_id": "SR-001",
      "component_type": "post_function",        // workflow_validator | script | listener | post_function | api_usage
      "plugin": "ScriptRunner",                  // ScriptRunner | JSU | native | Webhook | MISC
      "location": { "workflow": "Bug Workflow", "transition": "Resolve" },
      "features_detected": ["ComponentAccessor", "IssueManager"],
      "compatibility": { "cloud_status": "partial", "risk_level": "high" },
      "recommended_action": "Rewrite using Cloud REST API",
      "report_text": "Uses DC-only Java APIs...",
      "original_script": "import com.atlassian.jira..."
    }
  ]
}
```

## Things to be careful about

- **Don't modify**: block router dispatch logic, validator, packager, SSE event shape, or the React frontend contract
- **Output extension logic is duplicated** in `base_agent.py:_get_output_ext()` and `packager.py:get_file_extension()` — keep them in sync
- **CORS is wide open** (`allow_origins=["*"]`) — noted for production tightening
- **Gemini model is hardcoded** as `gemini-2.5-flash` in `BaseAgent.__init__()`
- The `docs/` folder and `chroma_db/` are local state — not committed to git
