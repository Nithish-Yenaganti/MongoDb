"""Offline tests for the Phase 1A gateway."""

import json
import unittest

from fastapi.testclient import TestClient

from api.main import TurnRequest, TurnService, create_app
from api.prompt_compiler import EpisodeMemory
from api.scoring import score


class FakeFireworks:
    def __init__(self) -> None:
        self.calls = []

    def stream(self, *, prompt, cache_key, model):
        self.calls.append(
            {"prompt": prompt, "cache_key": cache_key, "model": model}
        )
        yield "token", "Inspect the connection pool."
        yield "usage", {
            "prompt_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 80},
        }
        yield "perf", {"server-time-to-first-token": 0.025}


class FakeMetrics:
    def __init__(self) -> None:
        self.documents = []

    def write(self, document):
        self.documents.append(dict(document))


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.fireworks = FakeFireworks()
        self.metrics = FakeMetrics()
        self.service = TurnService(
            self.fireworks,
            self.metrics,
            EpisodeMemory(),
        )

    @staticmethod
    def request(mode="radixmind", turn=1, text="connection pool exhausted"):
        return {
            "episode_id": "episode-1",
            "turn": turn,
            "mode": mode,
            "chunk": {"source_type": "logs", "text": text},
            "question": "What is wrong?",
        }

    def test_score_is_phase_one_stub(self):
        self.assertEqual(score("anything", "logs"), 0.5)

    def test_post_turn_streams_and_writes_one_metrics_document(self):
        client = TestClient(create_app(self.service))
        response = client.post("/turn", json=self.request())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.headers["content-type"].startswith("text/event-stream")
        )
        self.assertIn("event: token", response.text)
        self.assertIn("event: metrics", response.text)
        self.assertEqual(len(self.metrics.documents), 1)
        self.assertEqual(self.metrics.documents[0]["cached_tokens"], 80)
        self.assertEqual(self.metrics.documents[0]["prompt_tokens"], 100)

    def test_both_modes_are_supported(self):
        client = TestClient(create_app(self.service))
        self.assertEqual(
            client.post("/turn", json=self.request("baseline")).status_code, 200
        )
        self.assertEqual(
            client.post("/turn", json=self.request("radixmind")).status_code, 200
        )
        self.assertEqual(len(self.metrics.documents), 2)

    def test_segment_b_is_append_only_and_ordered(self):
        first = TurnRequest.model_validate(self.request(turn=1, text="first"))
        second = TurnRequest.model_validate(self.request(turn=2, text="second"))
        list(self.service.run(first))
        list(self.service.run(second))
        prompt = self.fireworks.calls[-1]["prompt"]
        self.assertLess(prompt.index("first"), prompt.index("second"))
        self.assertLess(prompt.index("second"), prompt.index("SEGMENT C"))

    def test_cache_key_is_stable_per_episode_and_mode(self):
        first = TurnRequest.model_validate(self.request(turn=1))
        second = TurnRequest.model_validate(self.request(turn=2))
        list(self.service.run(first))
        list(self.service.run(second))
        self.assertEqual(
            self.fireworks.calls[0]["cache_key"],
            self.fireworks.calls[1]["cache_key"],
        )
        self.assertEqual(
            self.fireworks.calls[0]["cache_key"], "episode-1:radixmind"
        )

    def test_final_metrics_event_is_valid_json(self):
        request = TurnRequest.model_validate(self.request())
        events = "".join(self.service.run(request))
        metrics_data = events.split("event: metrics\ndata: ", 1)[1].split("\n", 1)[0]
        self.assertEqual(json.loads(metrics_data)["server_ttft_ms"], 25.0)


if __name__ == "__main__":
    unittest.main()
