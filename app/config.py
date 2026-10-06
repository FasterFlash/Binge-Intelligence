"""Central config — loads .env once, exposes typed accessors."""

import os
from dotenv import load_dotenv

load_dotenv()  # reads .env at project root

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://binge:binge_dev_pw@localhost:5433/binge_intelligence"
)

# ----- TMDB -----
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
if not TMDB_API_KEY:
    raise RuntimeError("TMDB_API_KEY missing from environment (.env)")

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"
TMDB_POSTER_SIZE = os.environ.get("TMDB_POSTER_SIZE", "w500")      # 300-500px, perfect for our cards
TMDB_BACKDROP_SIZE = os.environ.get("TMDB_BACKDROP_SIZE", "w1280")  # for future hero sections


# ----- MinIO (S3-compatible object storage) -----
MINIO_ENDPOINT   = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "")
MINIO_BUCKET     = os.environ.get("MINIO_BUCKET", "binge-content-docs")
MINIO_SECURE     = os.environ.get("MINIO_SECURE", "false").lower() == "true"

# Public URL base — this is what browsers / Streamlit will fetch images from.
# For a local MinIO on :9000 with a public-read bucket, the pattern is:
#   http://localhost:9000/<bucket>/<key>
MINIO_PUBLIC_BASE = os.environ.get(
    "MINIO_PUBLIC_BASE",
    ("https" if MINIO_SECURE else "http") + f"://{MINIO_ENDPOINT}",
)
