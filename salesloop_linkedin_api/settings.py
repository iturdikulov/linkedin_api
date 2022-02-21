import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGIN_TIMEOUT = float(os.getenv("LINKEDIN_API_LOGIN_TIMEOUT", 220))
REQUEST_TIMEOUT = float(os.getenv("LINKEDIN_API_REQUEST_TIMEOUT", 220))
CACHE_REQUESTS = bool(os.getenv("CACHE_REQUESTS", False))
