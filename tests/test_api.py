"""Offline tests for the Phase 1A gateway."""

import json
import unittest

from fastapi.testclient import TestClient

from api.main import TurnRequest, TurnService, create_app
from api.prompt_compiler import EpisodeMemory, SEGMENT_B_TOKEN_BUDGET
from api.scoring import PIN_THRESHOLD, score


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
        self.context_documents = []

    def write(self, document):
        self.documents.append(dict(document))

    def write_context(self, document):
        self.context_documents.append(dict(document))


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

    def test_real_scorer_prefers_runbook_to_log_dump(self):
        runbook_score = score("Follow this recovery procedure.", "runbook")
        log_score = score("2026-08-13T13:01:02Z rid=deadbeef", "log_dump")
        self.assertGreaterEqual(runbook_score, PIN_THRESHOLD)
        self.assertLess(log_score, PIN_THRESHOLD)

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

    def test_naive_mode_is_supported(self):
        client = TestClient(create_app(self.service))
        self.assertEqual(
            client.post("/turn", json=self.request("naive")).status_code, 200
        )

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

    def test_radixmind_pins_high_value_and_archives_low_value_context(self):
        high = TurnRequest.model_validate(
            self.request(turn=1, text="Follow the documented recovery procedure.")
        )
        high.chunk.source_type = "runbook"
        low = TurnRequest.model_validate(
            self.request(turn=2, text="2026-08-13T13:01:02Z rid=deadbeef")
        )
        low.chunk.source_type = "log_dump"
        list(self.service.run(high))
        list(self.service.run(low))
        self.assertEqual(
            [item["status"] for item in self.metrics.context_documents],
            ["pinned", "archived"],
        )
        self.assertIn("recovery procedure", self.fireworks.calls[-1]["prompt"])
        self.assertNotIn("deadbeef", self.fireworks.calls[-1]["prompt"])

    def test_segment_b_budget_archives_oversized_high_value_context(self):
        request = TurnRequest.model_validate(
            self.request(text="recovery " * (SEGMENT_B_TOKEN_BUDGET * 2))
        )
        request.chunk.source_type = "runbook"
        list(self.service.run(request))
        self.assertEqual(self.metrics.context_documents[0]["status"], "archived")
        self.assertNotIn("recovery recovery", self.fireworks.calls[0]["prompt"])

    def test_naive_prompt_prefix_changes_between_compilations(self):
        first = TurnRequest.model_validate(self.request(mode="naive", turn=1))
        second = TurnRequest.model_validate(self.request(mode="naive", turn=2))
        list(self.service.run(first))
        list(self.service.run(second))
        first_prefix = self.fireworks.calls[0]["prompt"].split("SEGMENT C", 1)[0]
        second_prefix = self.fireworks.calls[1]["prompt"].split("SEGMENT C", 1)[0]
        self.assertNotEqual(first_prefix, second_prefix)
        self.assertIn("OBSERVED_AT", second_prefix)

    def test_radixmind_segment_a_b_bytes_remain_a_prefix(self):
        first = TurnRequest.model_validate(self.request(turn=1, text="first"))
        second = TurnRequest.model_validate(self.request(turn=2, text="second"))
        list(self.service.run(first))
        list(self.service.run(second))
        first_prefix = self.fireworks.calls[0]["prompt"].split("SEGMENT C", 1)[0]
        second_prefix = self.fireworks.calls[1]["prompt"].split("SEGMENT C", 1)[0]
        self.assertTrue(second_prefix.startswith(first_prefix))


if __name__ == "__main__":
    unittest.main()
