from __future__ import annotations

import os

import redis
from langgraph.checkpoint.redis import RedisSaver

REDIS_URL = os.getenv(
    "ENNOSMART_CIR_REDIS_URL",
    "redis://127.0.0.1:6379/3",
)
GRAPH_REDIS_URL = os.getenv(
    "ENNOSMART_LANGGRAPH_REDIS_URL",
    "redis://127.0.0.1:6379/0",
)

print("[V3.21] Redis ping...")
client = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True,
)
if not client.ping():
    raise SystemExit(
        "Redis ne répond pas."
    )
print("[V3.21] Redis : OK")

print("[V3.21] Initialisation LangGraph RedisSaver...")
with RedisSaver.from_conn_string(
    GRAPH_REDIS_URL
) as checkpointer:
    checkpointer.setup()
print("[V3.21] LangGraph RedisSaver : OK")
