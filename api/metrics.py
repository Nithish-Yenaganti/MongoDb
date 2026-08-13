"""MongoDB metrics persistence."""

from typing import Any

from pymongo import ASCENDING, MongoClient


class MongoMetricsWriter:
    def __init__(self, mongo_uri: str) -> None:
        self.client = MongoClient(mongo_uri, serverSelectionTimeoutMS=8_000)
        self.collection = self.client["radixmind"].metrics
        self.collection.create_index(
            [("episode_id", ASCENDING), ("turn", ASCENDING)],
            name="episode_turn",
        )

    def write(self, document: dict[str, Any]) -> None:
        self.collection.insert_one(dict(document))
