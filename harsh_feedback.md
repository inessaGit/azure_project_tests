# Code Review: Legal Tagger — Technical & Security Analysis

Reviewed as a highly skeptical senior engineer. Organized by severity. No filler — every item is a real problem.

---

## Critical Bugs

---

### 1. Substring matching causes false positives on real words

`classifier.py:15` — `kw in text_lower` is a substring check, not a word-boundary check.

```python
# "nda" is a substring of "agenda"
classify_by_keywords("Please review the agenda before the meeting.")
# → "NDA"   ← WRONG

# "nda" is also a substring of "Hernandez", "Fernanda", "vendable", "amendable"
classify_by_keywords("The agenda for the Hernandez matter follows.")
# → "NDA"   ← WRONG, it's just a meeting agenda
```

Any document mentioning "agenda", "Hernandez", "Fernanda" will be classified NDA. This is a data correctness bug in a legal classification system.

**Fix:** Use word-boundary matching:

```python
import re

def classify_by_keywords(text: str) -> str:
    text_lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if re.search(r'\b' + re.escape(kw) + r'\b', text_lower):
                return category
    return "Other"
```

---

### 2. `classify_with_ai` returns unvalidated free text and stores it

`classifier.py:35` — The AI response is stripped and stored directly. GPT-4 does not always follow instructions.

```python
return response.choices[0].message.content.strip()
# If GPT returns "This appears to be an NDA document." → stored as category
# If GPT returns "" (empty after strip) → stored as empty string
# If GPT returns "NDA." → strip() doesn't remove the period → stored as "NDA."
# If GPT returns "Confidential" → stored, corrupts any downstream category check
```

`response.choices[0]` also raises `IndexError` if OpenAI returns an empty choices list (content filtering, error responses). Zero guard.

**Fix:**

```python
VALID_CATEGORIES = {"NDA", "MSA", "SOW", "Employment", "Other"}

def classify_with_ai(text: str) -> str:
    response = openai.chat.completions.create(...)
    if not response.choices:
        return "Other"
    raw = response.choices[0].message.content.strip()
    return raw if raw in VALID_CATEGORIES else "Other"
```

---

### 3. `Base.metadata.create_all` runs at import time, in tests

`main.py:5` — This line executes the moment any test imports `app.main`:

```python
Base.metadata.create_all(bind=engine)  # production DB engine, not the test engine
```

`conftest.py:25` does `from app.main import app` — which triggers this. Every test run creates `legal_tagger.db` in the project root using the production engine. The tests never touch this file (they override `get_db`), but it is created as a side effect.

Look at the project root right now — three DB files sitting there:
- `legal_tagger.db` — created by this import-time side effect
- `test_legal_tagger.db` — created by conftest
- `system_test_legal_tagger.db` — created by system tests

None are cleaned up between runs.

**Fix:** Use lifespan instead:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(lifespan=lifespan)
```

`create_all` now only fires when the server actually starts, not on import.

---

### 4. No error handling on OpenAI call — any failure is a 500

`classifier.py:22` — No try/except anywhere. If OpenAI returns 429 (rate limit), 503, or a network timeout, the exception propagates through `tag_document` and FastAPI returns a generic 500. The client cannot distinguish "server error" from "AI service temporarily unavailable."

Also: no `timeout` parameter is set. If OpenAI hangs, the request hangs indefinitely.

```python
def classify_with_ai(text: str) -> str:
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[...],
            timeout=10.0,  # missing entirely right now
        )
        ...
    except openai.RateLimitError:
        raise HTTPException(status_code=503, detail="Classification service temporarily unavailable")
    except openai.APITimeoutError:
        raise HTTPException(status_code=504, detail="Classification timed out")
    except openai.OpenAIError:
        return "Other"  # degrade gracefully
```

---

### 5. Route ordering: `/documents/` vs `/documents/{doc_id}` is a latent footgun

`router.py:26,34` — `GET /documents/{doc_id}` is registered before `GET /documents/`. Starlette's router tries routes in registration order. A request to `GET /documents/` could match `{doc_id}` with an empty string segment before reaching the list route. This currently works only because FastAPI validates `doc_id` as `int` and rejects the empty string with 422 — relying on type coercion to save you from a routing bug. Reverse the order: define `GET /documents/` before `GET /documents/{doc_id}`.

---

## Security Gaps

---

### 6. No authentication — documents are publicly enumerable

`router.py` — Every endpoint is completely unauthenticated. IDs are sequential integers. Any person who can reach the server can exfiltrate every document:

```bash
for i in $(seq 1 10000); do curl http://your-server/documents/$i; done
```

In a legal document system storing privileged attorney-client communications, this is a critical security flaw. At minimum, API key authentication:

```python
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key")

async def verify_api_key(api_key: str = Depends(api_key_header)):
    if api_key != settings.API_KEY:
        raise HTTPException(status_code=403)
```

---

### 7. No input size limit — trivial DoS

`models.py:6` — `content: str` has no maximum length. A client POSTs a 100MB document. FastAPI reads the entire body into memory, `classify_by_keywords` scans the whole thing, the content is stored in the DB as-is. Under concurrent load, memory exhaustion is trivial to trigger.

**Fix:**

```python
from pydantic import Field

class DocumentIn(BaseModel):
    content: str = Field(..., max_length=500_000)
    filename: str = Field(..., max_length=255)
```

---

### 8. Filename not sanitized — path traversal if ever written to disk

`models.py:9` — `filename: str` is stored as-is. Right now it's only in the DB, so not directly exploitable. But this is a document tagging service — the natural next feature is saving the actual file. If anyone adds `os.path.join(upload_dir, doc.filename)` and `filename` is `"../../../etc/cron.d/malicious"`, that is a path traversal.

**Fix:**

```python
import os

@field_validator("filename")
@classmethod
def filename_safe(cls, v: str) -> str:
    if os.path.basename(v) != v:
        raise ValueError("Filename must not contain path separators")
    return v
```

---

### 9. Document content sent to OpenAI — no data privacy consideration

`classifier.py:32` — Legal documents containing privileged attorney-client communications, PII, financial information, and trade secrets are forwarded to OpenAI's API over the public internet. There is no:

- PII scrubbing before sending
- Data processing agreement (required for GDPR, HIPAA, and attorney-client privilege)
- Consideration of OpenAI's data retention policy (default: 30 days for API calls)
- Option to use Azure OpenAI, which offers private endpoints, data residency, and enterprise DPA

For a law firm like MWE, this is a compliance blocker. The architecture decision to use a public AI API for document classification must be a conscious, documented choice — not an accident.

---

### 10. Hardcoded database URL and model name

`database.py:4`, `classifier.py:23`:

```python
DATABASE_URL = "sqlite:///./legal_tagger.db"  # hardcoded
model="gpt-4"                                  # hardcoded
```

Neither is configurable without editing source code. You cannot configure different databases for dev/staging/prod. You cannot switch models for cost or capability reasons. These must be environment variables with validation at startup:

```python
import os
DATABASE_URL = os.environ["DATABASE_URL"]               # fail fast if missing
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4")  # with a default
```

---

## Production Readiness Issues

---

### 11. `GET /documents/` has no pagination

`router.py:35` — Returns all documents in a single query with no `limit`, `offset`, or cursor. With 100k documents at average 10KB each, this is a 1GB response that loads the entire table into memory. Also: `DocumentOut` includes full `content` in every list item — list endpoints should return summary models, not full payloads.

**Fix:**

```python
@router.get("/documents/", response_model=list[DocumentOut])
def list_documents(skip: int = 0, limit: int = Query(default=20, le=100), db: Session = Depends(get_db)):
    return db.query(db_models.Document).offset(skip).limit(limit).all()
```

---

### 12. Health endpoint doesn't actually check health

`main.py:11` — A load balancer using this endpoint will route traffic to the service even if the database is down:

```python
@app.get("/health")
def health():
    return {"status": "ok"}  # always returns ok
```

**Fix:** Make it verify the DB is reachable:

```python
from sqlalchemy import text

@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))  # raises if DB is unreachable
    return {"status": "ok"}
```

---

### 13. No timestamps on documents

`db_models.py` — No `created_at` column. In a legal document system, "when was this tagged" is essential for audit trails, compliance, and debugging. This is a missing basic requirement, not a nice-to-have:

```python
from sqlalchemy import DateTime
from datetime import datetime, timezone

created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
```

---

### 14. Blocking I/O in a sync route — thread pool exhaustion

`router.py:13` — `def tag_document(...)` is sync. FastAPI runs sync handlers in a thread pool (default: ~40 threads). An OpenAI call takes 3–10 seconds. With 40 concurrent requests hitting the AI fallback path, the entire thread pool is exhausted. Subsequent requests queue and time out.

**Fix:** Make the route `async def` and use the async OpenAI client:

```python
async def tag_document(doc: DocumentIn, db: Session = Depends(get_db)):
    category = await classify_document_async(doc.content)  # async OpenAI call
```

---

### 15. SQLite is not viable for concurrent writes

`database.py` — SQLite serializes all writes. Under concurrent POSTs, only one write executes at a time — every other request blocks waiting for the write lock. More critically: if you run two uvicorn workers (standard production deployment), they will corrupt the database. `check_same_thread=False` turns off SQLite's own safety guard.

SQLite is fine for local dev and tests. It is not appropriate for a multi-threaded, multi-process production service. The database URL being hardcoded (see issue #10) makes this impossible to swap without code changes.

---

### 16. `db.refresh()` after commit is a wasted query

`router.py:22` — After `db.commit()`, the ORM object already has all values populated (SQLAlchemy fetches the generated `id` as part of the flush). `db.refresh()` issues a second `SELECT` to reload what we just committed. Remove it.

---

### 17. No category constraint in the database

`db_models.py:11` — `category = Column(String(50), nullable=False)` accepts any string. Combined with issue #2 (unvalidated AI response), invalid categories are stored with no DB-level enforcement. The DB should reject bad values at the storage layer:

```python
from sqlalchemy import Enum

category = Column(
    Enum("NDA", "MSA", "SOW", "Employment", "Other", name="category_enum"),
    nullable=False,
)
```

---

### 18. `Optional` imported but unused

`models.py:3` — `from typing import Optional` — nothing in the file uses it. Dead import.

---

## Test-Specific Issues

---

### 19. `test_all_documents_retrievable` passes vacuously when run in isolation

`test_tagging_workflow.py:90` — The test iterates over `TestBatchUploadWorkflow.uploaded_ids`. If run alone (without the preceding upload tests populating the dict), `uploaded_ids` is empty. The loop body never executes. The test passes with zero assertions — a silent false positive.

```python
for doc_type, doc_id in TestBatchUploadWorkflow.uploaded_ids.items():
    # empty dict → never runs → test "passes"
    resp = system_client.get(f"/documents/{doc_id}")
    assert resp.status_code == 200
```

**Fix:** Add an explicit non-empty guard before the loop, or restructure to not rely on class-level mutable state.

---

### 20. Mutable class variable as shared test state

`test_tagging_workflow.py:58` — `uploaded_ids: dict = {}` is a class variable. Class variables are shared across all instances and persist for the lifetime of the process. If you run the test class twice in the same pytest session (unusual but possible with plugins), the dict from the first run persists into the second. This is implicit global state inside a test.

**Fix:** Don't share state between tests at all. Each test creates its own data and uses the returned IDs directly.

---

### 21. Test DB files are created relative to CWD, never cleaned up

`conftest.py:29`, `test_tagging_workflow.py:21` — Both create `.db` files at a path relative to wherever pytest is invoked. These files persist between runs. A crash mid-run leaves them in an inconsistent state. The project root currently has three `.db` files that are test artifacts.

**Fix:** Use true in-memory SQLite with `StaticPool`:

```python
from sqlalchemy.pool import StaticPool

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,  # forces all connections to share one in-memory DB
)
```

No file created. No cleanup needed. Faster.

---

### 22. Golden dataset tests `classify_by_keywords` directly, skips the pipeline

`test_regression_suite.py:77` — `classify_by_keywords` is called, not `classify_document`. The "Other" entry (`"The party of the first part hereby agrees..."`) would trigger `classify_with_ai` in production, but the test bypasses that entirely. A bug in the fallback routing logic in `classify_document` would not be caught by any golden dataset test.

---

### 23. Performance threshold is so loose it's not useful

`test_regression_suite.py:150` — `assert elapsed < 1.0` for 1000 simple string operations. Actual runtime is ~0.001s. The threshold allows a 1000x regression before failing. This is a test that gives false confidence. Set it to `< 0.05s` which is still 50x headroom but would catch a real performance regression.

---

### 24. `from unittest.mock import patch` in regression test is a dead import

`test_regression_suite.py:14` — `patch` is imported but never called directly in this file. The API regression tests use the `client` fixture, which doesn't need explicit patching (the NDA content matches keywords, no AI call is made). Remove it.

---

### 25. Dev/test dependencies not separated from production

`requirements.txt` — `pytest` and `pytest-cov` are listed alongside production dependencies. The production container will install test tooling it doesn't need. Split into `requirements.txt` (production) and `requirements-dev.txt` (development and testing).

---

## How a Request Actually Flows Through the Backend

For `POST /documents/` with `{"content": "non-disclosure agreement...", "filename": "nda.pdf"}`:

```
Client (curl / browser / service)
    │
    │  TCP connection to port 8000
    │  HTTP/1.1 POST /documents/
    │  Content-Type: application/json
    │  {"content": "...", "filename": "nda.pdf"}
    ▼
Uvicorn (ASGI server)
    │  Receives bytes from socket
    │  Parses HTTP framing (method, path, headers, body)
    │  Creates ASGI scope dict
    │  Calls Starlette's ASGI application
    ▼
FastAPI / Starlette router
    │  Matches path "/documents/" + method POST → tag_document handler
    │  Handler is def (not async def) → wraps in threadpool executor
    │  Invokes FastAPI's Dependency Injection system
    ▼
Dependency Injection: get_db()
    │  SessionLocal() acquires a connection from the SQLAlchemy pool
    │  Wraps it in a Session object
    │  Yields session to route handler
    ▼
Pydantic validation (DocumentIn)
    │  JSON bytes deserialized to Python dict
    │  dict validated against DocumentIn schema
    │  If validation fails → 422 Unprocessable Entity, handler never called
    ▼
tag_document() executes in thread pool
    │
    ├─► classify_document(doc.content)
    │       │
    │       ├─► classify_by_keywords(text)
    │       │     text.lower() → O(n*k) substring scan
    │       │     Returns "NDA" if match found → done, no network call
    │       │
    │       └─► [if keywords return "Other"] classify_with_ai(text)
    │                 │
    │                 │  OUTBOUND HTTPS POST to api.openai.com:443
    │                 │  TLS handshake (new connection) or reuse from pool
    │                 │  Request body: {"model": "gpt-4", "messages": [...]}
    │                 │  Authorization: Bearer <OPENAI_API_KEY>
    │                 │  content truncated to text[:2000] chars (~500 tokens)
    │                 │
    │                 │  [waits 500ms–5000ms for response — no timeout set ← bug]
    │                 │
    │                 │  Response: {"choices": [{"message": {"content": "NDA"}}]}
    │                 │  Returns response.choices[0].message.content.strip()
    │                 ▼
    │             [network round trip to OpenAI: 500ms–5s, blocks the thread]
    │
    ├─► db_models.Document(filename=..., content=..., category=...)
    │     Creates ORM object in memory, not yet in DB
    │
    ├─► db.add(db_doc)
    │     Marks object as pending INSERT in session's identity map
    │
    ├─► db.commit()
    │     Flushes pending changes → executes SQL
    │     SQLite: acquires exclusive write lock
    │     INSERT INTO documents (filename, content, category) VALUES (?, ?, ?)
    │     Releases write lock, transaction committed
    │     [concurrent writes block here waiting for the lock ← scalability issue]
    │
    ├─► db.refresh(db_doc)
    │     SELECT * FROM documents WHERE id = ?   ← unnecessary second round trip
    │     Populates db_doc.id from DB autoincrement
    │
    └─► return db_doc
          │
          FastAPI serializes db_doc (ORM object) → DocumentOut (Pydantic)
          from_attributes=True reads ORM column values as dict fields
          Pydantic serializes to JSON bytes
          ▼
      HTTP/1.1 200 OK
      Content-Type: application/json
      {"id": 1, "filename": "nda.pdf", "category": "NDA", "content": "..."}
          ▼
      Uvicorn writes bytes to TCP socket → client receives response
```

**Key things to understand in this flow:**

- `def` (sync) route handlers run in a thread pool — not the async event loop. The thread blocks during the OpenAI call. Under concurrency, exhausting the thread pool stalls all requests, including those not needing AI.
- `db.refresh()` is a second database round trip after `db.commit()`. It is unnecessary — SQLAlchemy populates the generated `id` after commit.
- The OpenAI call is the only call that leaves your infrastructure. All other I/O (SQLite reads/writes) is local file system.
- SQLite's write lock means concurrent POSTs serialize at the DB layer. Under load, every request beyond the first waits for the previous write to complete before it can begin.
- The dependency injection chain (`get_db` → `SessionLocal()`) creates one new DB session per request and closes it in the `finally` block after the response is sent.

---

## Summary Table

| # | Issue | Severity | File |
|---|---|---|---|
| 1 | Substring false positives ("agenda" → NDA) | Critical | classifier.py |
| 2 | AI response not validated against enum | Critical | classifier.py |
| 3 | `create_all` runs at import time, creates production DB in tests | Critical | main.py |
| 4 | No error handling or timeout on OpenAI call | High | classifier.py |
| 5 | Route ordering: GET /documents/ vs /{doc_id} latent footgun | High | router.py |
| 6 | No authentication — documents enumerable by sequential ID | High / security | router.py |
| 7 | No input size limit — trivial DoS via large body | High / security | models.py |
| 8 | Filename not sanitized — path traversal risk | High / security | models.py |
| 9 | Document content sent to OpenAI, no compliance consideration | High / compliance | classifier.py |
| 10 | Hardcoded DB URL and model name — not configurable | Medium | database.py, classifier.py |
| 11 | No pagination on list endpoint — OOM at scale | Medium | router.py |
| 12 | Health endpoint doesn't check DB | Medium | main.py |
| 13 | No timestamps on documents | Medium | db_models.py |
| 14 | Blocking I/O in sync route — thread pool exhaustion | Medium | router.py |
| 15 | SQLite not viable for concurrent writes / multiple workers | Medium | database.py |
| 16 | `db.refresh()` is a wasted SELECT after every INSERT | Low | router.py |
| 17 | No category CHECK constraint in DB | Low | db_models.py |
| 18 | `Optional` imported but unused | Low | models.py |
| 19 | Vacuously passing test when run in isolation | Medium / tests | test_tagging_workflow.py |
| 20 | Mutable class variable as shared test state | Medium / tests | test_tagging_workflow.py |
| 21 | Test DB files persist on disk, never cleaned up | Medium / tests | conftest.py, system tests |
| 22 | Golden dataset tests keyword path only, skips pipeline | Medium / tests | test_regression_suite.py |
| 23 | Performance threshold too loose to catch real regressions | Low / tests | test_regression_suite.py |
| 24 | Dead `patch` import in regression tests | Low / tests | test_regression_suite.py |
| 25 | Dev/test deps mixed with production deps | Low | requirements.txt |
