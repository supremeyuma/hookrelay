"""Redis token bucket: atomic refill and consume via a Lua script."""

import time
from dataclasses import dataclass

from redis.asyncio import Redis

# KEYS[1] bucket key
# ARGV[1] capacity (burst), ARGV[2] refill tokens/second,
# ARGV[3] current unix time (float), ARGV[4] key TTL seconds
_CONSUME_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])

if tokens == nil then
  tokens = capacity
  ts = now
end

local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * refill)

local allowed = 0
local retry_after = 0

if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry_after = math.ceil((1 - tokens) / refill)
end

redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, ttl)

return {allowed, retry_after}
"""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int


class TokenBucket:
    """Per-source limiter backed by Redis so every API replica shares state."""

    def __init__(self, redis: Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis

    async def consume(self, key: str, *, rate_per_second: float, burst: int) -> RateLimitDecision:
        ttl = max(60, int(burst / max(rate_per_second, 0.001)) + 60)
        result = await self._redis.eval(  # type: ignore[no-untyped-call]
            _CONSUME_SCRIPT,
            1,
            key,
            str(burst),
            str(rate_per_second),
            str(time.time()),
            str(ttl),
        )
        allowed, retry_after = result
        return RateLimitDecision(
            allowed=bool(int(allowed)),
            retry_after_seconds=max(0, int(retry_after)),
        )
