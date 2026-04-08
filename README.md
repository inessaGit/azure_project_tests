# azure_project_tests

A FastAPI service for legal document classification — built as a hands-on testing practice project. The focus is writing production-quality tests across all four layers: unit, integration, system, and regression.

The domain is intentionally realistic: a legal document tagger that classifies contracts (NDA, MSA, SOW, Employment, Other) using keyword matching with an AI fallback. This mirrors the kind of AI-assisted document workflow common in legal tech environments.

---

## What Is Being Tested

The application has two components worth testing independently, and then together:

**`app/classifier.py`** — the classification logic
- `classify_by_keywords(text)` — fast keyword scan. Pure function, no dependencies.
- `classify_with_ai(text)` — OpenAI fallback for ambiguous documents.
- `classify_document(text)` — orchestrator: tries keywords first, falls back to AI.

**`app/router.py`** — the FastAPI routes
- `POST /documents/` — classifies a document and persists it to the database.
- `GET /documents/{id}` — retrieves a stored document by ID.
- `GET /documents/` — lists all stored documents.

**Why these two?** They represent a clean boundary: pure logic vs. I/O. Unit tests cover logic in isolation. Integration tests verify the logic connects correctly to the database through the API. System tests verify the whole thing works as a user would experience it.

---

## Test Architecture

```
tests/
├── conftest.py               # shared fixtures — TestClient, DB override, env setup
├── unit/
│   └── test_classifier.py    # 18 tests — pure logic, all deps mocked
├── integration/
│   └── test_api_db.py        # 14 tests — API routes + real SQLite DB
├── system/
│   └── test_tagging_workflow.py   # 11 tests — full stack, workflow scenarios
└── regression/
    └── test_regression_suite.py   # 17 tests — golden dataset + bug guards + perf
```

### Layer 1 — Unit Tests (`tests/unit/`)

**What:** Each function in `classifier.py` tested in complete isolation. Every external dependency (OpenAI) is replaced with a mock.

**Why this layer exists:** Pure logic bugs are cheapest to catch here. A failing unit test points at exactly one function and one input. No database, no network, no ambiguity about where it broke.

**Key patterns used:**
- `@pytest.mark.parametrize` — runs one test function against 10 input/expected pairs. Covers happy path, edge cases, case variants, empty input.
- `unittest.mock.patch` — replaces `openai.chat.completions.create` with a `MagicMock`. The real API is never called.
- `mock.assert_not_called()` — verifies the AI is skipped when a keyword match is found (cost and latency guard).
- `mock.assert_called_once()` — verifies the AI *is* invoked on the fallback path.

**What is asserted:**
- Correct category returned for every keyword variant and case combination
- Empty and whitespace-only input returns `"Other"` without raising
- Very long input (50,000 words) processes without timeout or error
- AI response whitespace is stripped before returning
- Input is truncated to 2000 chars before being sent to the model
- The correct model name (`gpt-4`) is passed to the API call
- Orchestration decision: AI called only when keyword matching returns `"Other"`

---

### Layer 2 — Integration Tests (`tests/integration/`)

**What:** The API route handler and database tested as a real pair. Routes run, SQLAlchemy executes real SQL against a SQLite test database. Only OpenAI is still mocked.

**Why this layer exists:** Unit tests pass even when the wiring between components is broken. Integration tests catch: wrong SQL, missing DB columns, ORM misconfiguration, response schema mismatches, and validation errors that only surface when Pydantic processes real request data.

**Key patterns used:**
- `conftest.py` `db_session` fixture (`scope="function"`) — creates all tables before each test, drops them after. Every test starts with an empty database. No test can be affected by another's data.
- `app.dependency_overrides[get_db]` — replaces the production database dependency with the test database session. This is FastAPI's built-in testing mechanism. It is always cleared after each test.
- `TestClient` from `fastapi.testclient` — makes real HTTP requests to the app in-process. No server needed.

**What is asserted:**
- `POST /documents/` returns 200 with correct category and a DB-assigned integer ID
- Submitted content is stored exactly as received
- Missing required fields return 422 (Pydantic validation), not 500
- Empty request body returns 422
- Ambiguous document triggers exactly one AI call and stores the result
- `GET /documents/{id}` round-trips correctly after a POST
- `GET /documents/99999` returns 404 with the correct detail message
- `GET /documents/not-a-number` returns 422 (FastAPI path param validation)
- DB is confirmed empty at the start of each test (isolation check)
- Sequential inserts produce distinct IDs (autoincrement working)
- `GET /documents/` returns an empty list on a fresh DB, and all docs after inserts

---

### Layer 3 — System Tests (`tests/system/`)

**What:** The full application stack treated as a black box. Tests interact via HTTP only — no direct DB access, no internal function calls. Simulates real user workflows.

**Why this layer exists:** Integration tests verify components connect. System tests verify the *experience* is correct. They also catch bugs that only appear when the full stack runs: middleware interactions, response serialization edge cases, error propagation through multiple layers.

**Scope is `"module"`** (not `"function"`) — the database persists across all tests in the file. Tests within a class can build on each other's state, mirroring a real user session (upload a batch, then retrieve the batch).

**Scenarios tested:**
- **Batch upload workflow** — three documents uploaded in sequence, each classified correctly, all retrievable by ID, all present in the list endpoint
- **AI fallback workflow** — ambiguous document triggers AI, result stored, persisted category matches what AI returned
- **Error scenarios** — malformed request returns 422 not 500, missing document returns 404, health endpoint always returns ok, empty content is accepted and classified

**What is asserted:** Status codes, response body structure, correct category values, error detail messages — everything a real API consumer would observe.

---

### Layer 4 — Regression Tests (`tests/regression/`)

**What:** Tests that permanently lock in correct behavior. Two categories: a golden dataset (hand-reviewed inputs with expected outputs) and individual bug regression guards (one test per historical bug fix).

**Why this layer exists:** Code changes break things that used to work. Without regression tests, you only find out when a user reports it. This suite runs on every commit in CI. Any failure means something previously correct is now broken — the test name tells you exactly which behavior regressed and when it was originally fixed.

**Golden dataset** — 10 documents covering all categories, including edge cases that were previously bugs (all-caps variants, unhyphenated "non disclosure", mixed-category documents). Every entry was confirmed correct by hand. Adding a new document type or changing keyword logic requires this dataset to still pass.

**Individual bug guards** — each test is named and documented with the bug it guards:
- `test_regression_bug_uppercase_nda_not_matched` — uppercase wasn't matched before `.lower()` normalization was added
- `test_regression_bug_nda_without_hyphen_not_matched` — "non disclosure" (no hyphen) was missing from the keyword list
- `test_regression_bug_first_match_priority` — category returned was non-deterministic when multiple keywords matched
- `test_regression_empty_string_returns_other_not_error` — empty string caused a `KeyError` in early versions

**Performance regression guard** — 1000 classifications must complete in under 1 second. Catches accidentally introduced O(n²) behavior or regex backtracking.

**API regression guards** — verify response field names and error message formats don't change silently (consumers may depend on them).

---

## Project Structure

```
azure_project_tests/
├── app/
│   ├── __init__.py
│   ├── classifier.py     # classification logic (keyword + AI)
│   ├── database.py       # SQLAlchemy engine, session, get_db dependency
│   ├── db_models.py      # SQLAlchemy ORM model (Document table)
│   ├── main.py           # FastAPI app, DB init on startup
│   ├── models.py         # Pydantic request/response schemas
│   └── router.py         # route handlers
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   └── test_classifier.py
│   ├── integration/
│   │   └── test_api_db.py
│   ├── system/
│   │   └── test_tagging_workflow.py
│   └── regression/
│       └── test_regression_suite.py
├── pytest.ini
├── requirements.txt
└── README.md
```

---

## Setup

**Requirements:** Python 3.9+

```bash
git clone https://github.com/inessaGit/azure_project_tests.git
cd azure_project_tests
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

No environment variables needed to run tests. `conftest.py` sets a dummy `OPENAI_API_KEY` automatically — the key is never used because all OpenAI calls are mocked.

To run the service locally (optional):
```bash
OPENAI_API_KEY=your-key uvicorn app.main:app --reload
```

---

## Running Tests

**All tests:**
```bash
pytest
```

**By layer:**
```bash
pytest tests/unit/
pytest tests/integration/
pytest tests/system/
pytest tests/regression/
```

**By marker (fastest subset for CI):**
```bash
pytest -m regression        # golden dataset + bug guards — runs on every commit
pytest -m "not system"      # skip the module-scoped slow tests
```

**With coverage:**
```bash
pytest --cov=app --cov-report=term-missing
```

**Stop on first failure (useful during development):**
```bash
pytest -x
```

**Expected output:**
```
60 passed in 0.16s
```

---

## Key Design Decisions

**Why SQLite for tests?** No external service needed. In-memory equivalent via file that's created and dropped per test. The same SQLAlchemy code that runs against Postgres in production runs against SQLite in tests — the ORM abstracts the difference for the operations used here.

**Why mock OpenAI instead of using a real key?** Tests must be deterministic, fast, and runnable without credentials. Mocking OpenAI also lets tests assert *how* the function is called (correct model, correct truncation) — not just what it returns.

**Why `dependency_overrides` instead of monkeypatching the DB?** It's FastAPI's intended testing mechanism. It replaces the dependency at the injection point, so every route that uses `get_db` automatically gets the test database. No surgery on internal functions needed.

**Why `scope="module"` in system tests?** System tests simulate a user session — a paralegal uploads three documents and then retrieves them. That requires shared state across tests within a scenario. Function scope would reset the DB between `test_upload_nda` and `test_all_documents_retrievable`, breaking the scenario. The isolation cost is acceptable because system tests aren't meant to be fully independent — they tell a story.

**Why document the bug in each regression test?** A test named `test_regression_bug_nda_without_hyphen_not_matched` with a comment explaining the fix date is self-documenting history. Six months later, if someone removes the `"non disclosure"` keyword variant thinking it's redundant, the test name tells them exactly why it exists.
