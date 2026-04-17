import time
from collections import OrderedDict


class LRUDedup:
    """Bounded TTL cache for MQTT command deduplication.

    Keyed on (command_id, topic) to survive fan-out when the same command
    id is delivered across building/floor/room command subscriptions.
    """

    def __init__(self, capacity: int = 4096, ttl_seconds: float = 60.0):
        self.capacity = capacity
        self.ttl = ttl_seconds
        self._entries: "OrderedDict[tuple, float]" = OrderedDict()
        self._total = 0
        self._dup_hits = 0

    def seen(self, command_id: str, topic: str) -> bool:
        self._total += 1
        now = time.monotonic()
        self._evict(now)
        key = (command_id, topic)
        if key in self._entries:
            self._dup_hits += 1
            self._entries.move_to_end(key)
            return True
        self._entries[key] = now
        if len(self._entries) > self.capacity:
            self._entries.popitem(last=False)
        return False

    def _evict(self, now: float) -> None:
        cutoff = now - self.ttl
        while self._entries:
            oldest_key, oldest_ts = next(iter(self._entries.items()))
            if oldest_ts >= cutoff:
                break
            self._entries.popitem(last=False)

    def metrics(self) -> dict:
        return {
            "total": self._total,
            "dup_hits": self._dup_hits,
            "size": len(self._entries),
        }
