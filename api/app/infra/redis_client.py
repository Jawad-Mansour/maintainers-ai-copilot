"""Redis client factory.

Two logical databases:
  DB 0 — conversation short-term memory (24h TTL)
  DB 1 — API response cache (5min TTL)
"""

from __future__ import annotations

import redis.asyncio as aioredis

CONVERSATION_DB = 0
CACHE_DB = 1

CONVERSATION_TTL = 86_400  # 24 hours — full working day; overnight gap clears stale context
CACHE_TTL = 300  # 5 minutes — GET /me, GET /conversations rarely change


def build_redis(host: str, port: int = 6379, db: int = CONVERSATION_DB) -> aioredis.Redis:
    return aioredis.Redis(host=host, port=port, db=db, decode_responses=True)


def build_cache_redis(host: str, port: int = 6379) -> aioredis.Redis:
    return aioredis.Redis(host=host, port=port, db=CACHE_DB, decode_responses=True)
