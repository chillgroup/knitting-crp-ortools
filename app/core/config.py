import os

REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "http://backend:8082/api/webhook/solver")
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))
