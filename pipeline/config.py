import os

# --- Paths (relative to this package -> 09-14/hi) ---
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT = os.path.join(ROOT, "outputs", "evidence_vault")
STATE = os.path.join(ROOT, "outputs", "state")
LOGS = os.path.join(ROOT, "outputs", "logs")
AWARD_DIR = os.path.join(ROOT, "outputs", "award_reports")

# --- PostgreSQL ---
DB = dict(host="localhost", port=5432, dbname="trading_data",
          user="postgres", password=os.environ.get("EKMI_DB_PASSWORD", ""))

# --- DeepSeek ---
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MAX_TOKENS = int(os.environ.get("EKMI_DEEPSEEK_MAX_TOKENS", "1500"))

# --- Pipeline tuning (all knobs in one place) ---
MAX_WORKERS = int(os.environ.get("EKMI_MAX_WORKERS", "5"))
REQUEST_DELAY_SECONDS = float(os.environ.get("EKMI_REQUEST_DELAY", "0.3"))
RETRIES = int(os.environ.get("EKMI_RETRIES", "3"))
CIRCUIT_FAILURES = int(os.environ.get("EKMI_CIRCUIT_FAILURES", "5"))
CIRCUIT_COOLDOWN_SECONDS = float(os.environ.get("EKMI_CIRCUIT_COOLDOWN", "300"))
DISCOVERY_MIN_INTERVAL_SECONDS = float(os.environ.get("EKMI_DISCOVERY_INTERVAL", "3600"))
DEEPSEEK_TIMEOUT = int(os.environ.get("EKMI_DEEPSEEK_TIMEOUT", "120"))
DEEPSEEK_CHUNK_SIZE = int(os.environ.get("EKMI_DEEPSEEK_CHUNK", "6000"))

# --- Trust convention (existing DB fields only, no schema change) ---
ACTOR = "codex_fresh_v2"

# government/PSU authority hosts accepted as real evidence
GOV_HOSTS = {
    "rites.com", "www.rites.com", "dfccil.com", "www.dfccil.com",
    "backend.delhimetrorail.com", "www.delhimetrorail.com",
    "kochimetro.org", "chennaimetrorail.org", "bmrc.co.in", "mmrcl.com",
}

os.makedirs(VAULT, exist_ok=True)
os.makedirs(STATE, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)
os.makedirs(AWARD_DIR, exist_ok=True)
