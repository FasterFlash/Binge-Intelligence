"""Central config — loads .env once, exposes typed accessors."""

import os
from dotenv import load_dotenv

load_dotenv()  # reads .env at project root

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://binge:binge_dev_pw@localhost:5433/binge_intelligence"
)

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
if not TMDB_API_KEY:
    raise RuntimeError("TMDB_API_KEY missing from environment (.env)")

TMDB_BASE_URL = "https://api.themoviedb.org/3"