"""MongoDB metrics and context-ledger persistence."""
# mongodb/api/metrics.py
from typing import Any

from pymongo import ASCENDING, MongoClient


class MongoMetricsWriter:
    def __init__(self, mongo_uri: str) -> None:
        self.client = MongoClient(mongo_uri, serverSelectionTimeoutMS=8_000)
        self.collection = self.client["radixmind"].metrics
        self.ledger = self.client["radixmind"].context_ledger
        self.collection.create_index(
            [("episode_id", ASCENDING), ("turn", ASCENDING)],
            name="episode_turn",
        )
        self.ledger.create_index(
            [("content_hash", ASCENDING)],
            unique=True,
            name="uniq_content_hash",
        )

    def write(self, document: dict[str, Any]) -> None:
        self.collection.insert_one(dict(document))

    def write_context(self, document: dict[str, Any]) -> None:
        """Upsert one scored chunk without duplicating identical content."""
        payload = dict(document)
        content_hash = payload.pop("content_hash")
        self.ledger.update_one(
            {"content_hash": content_hash},
            {
                "$set": payload,
                "$setOnInsert": {
                    "content_hash": content_hash,
                    "reuse_count": 0,
                },
            },
            upsert=True,
        )
