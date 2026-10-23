-- A single provider-wide sliding window. TIME avoids worker clock differences.
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000000 + tonumber(clock[2])
local window = tonumber(ARGV[1]) * 1000
local limit = tonumber(ARGV[2])

-- If Redis starts empty, wait one window. Old provider requests may still count.
local ready_at = redis.call('GET', KEYS[2])
if not ready_at then
    ready_at = now + window
    redis.call('SET', KEYS[2], string.format('%.0f', ready_at))
end
if now < tonumber(ready_at) then
    return {0, math.ceil((tonumber(ready_at) - now) / 1000), now}
end

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
if redis.call('ZCARD', KEYS[1]) < limit then
    redis.call('ZADD', KEYS[1], now, ARGV[3])
    -- Retain slightly longer than the window; scores decide admission, not TTL rounding.
    redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[1]) + 1000)
    return {1, 0, now}
end
local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
return {0, math.ceil((tonumber(oldest[2]) + window - now) / 1000), now}
