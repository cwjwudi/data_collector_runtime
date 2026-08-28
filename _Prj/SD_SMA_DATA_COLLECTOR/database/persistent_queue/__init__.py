"""Durable SQLite outbox used by the collector storage pipeline."""

from .queue_store import PersistentQueueStore, QueueCapacityError

__all__ = ["PersistentQueueStore", "QueueCapacityError"]
