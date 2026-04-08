import os

# All runtime configuration read from environment variables.
# Defaults are for local development only — never use SQLite or a blank API_KEY in production.

API_KEY: str = os.environ.get("API_KEY", "")
DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite:///./legal_tagger.db")
OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4")
