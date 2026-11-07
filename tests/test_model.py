from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from shuyi.contracts import Assignment, ClassificationBatch, ShuyiError
from shuyi.layout.model import OpenAIClassifier
from shuyi.layout.protocol import strict_json, validate_batch, validate_observations
from tests.test_layout import make_node


def response(content, *, finish="stop", usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)],
        usage=usage,
    )


class ProtocolTests(unittest.TestCase):
    def test_exact_ids_keys_and_heading_levels(self):
        invalid = [
            [],
            [{"id": "other", "role": "body", "level": None}],
            [{"id": "n000001", "role": "body", "level": None, "reason": "extra"}],
            [{"id": "n000001", "role": "heading", "level": True}],
            [{"id": "n000001", "role": "heading", "level": 7}],
            [{"id": "n000001", "role": "heading", "level": None}],
            [{"id": "n000001", "role": "body", "level": 1}],
            [{"id": "n000001", "role": None, "level": 1}],
            [{"id": "n000001", "role": ["body"], "level": None}],
        ]
        for value in invalid:
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ShuyiError, "model_response_invalid"),
            ):
                validate_observations(value, ["n000001"])
        duplicate = [{"id": "n000001", "role": None, "level": None}] * 2
        with self.assertRaises(ShuyiError):
            validate_observations(duplicate, ["n000001", "n000002"])
        valid = validate_observations(
            [{"id": "n000001", "role": "heading", "level": 2}], ["n000001"]
        )
        self.assertEqual(valid, (Assignment("n000001", "heading", 2),))

    def test_duplicate_json_keys_and_non_json_fail(self):
        for text in ('{"observations":[],"observations":[]}', "not JSON", "```json\n{}\n```", "{"):
            with self.assertRaises(ShuyiError):
                strict_json(text)

    def test_usage_is_strict_nonnegative_integer(self):
        for usage in (
            {"total_tokens": -1},
            {"total_tokens": True},
            {"total_tokens": 1.5},
            {"total_tokens": "1"},
        ):
            batch = ClassificationBatch((Assignment("n000001", "body"),), usage)
            with self.assertRaises(ShuyiError):
                validate_batch(batch, [make_node(1)])


class OpenAITransportTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.factory = patch("openai.OpenAI").start()
        self.addCleanup(patch.stopall)
        self.factory.return_value.__enter__.return_value = self.client
        self.classifier = OpenAIClassifier("https://model.example/v1", "example", "secret-key")

    def test_strict_transport_and_usage(self):
        content = json.dumps({"observations": [{"id": "n000001", "role": "body", "level": None}]})
        self.client.chat.completions.create.return_value = response(
            content, usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18)
        )
        batch = self.classifier.classify([make_node(1)])
        self.assertEqual(batch.observations, (Assignment("n000001", "body"),))
        self.assertEqual(batch.usage["total_tokens"], 18)
        self.assertEqual(self.factory.call_args.kwargs["timeout"], 60.0)
        self.assertEqual(self.factory.call_args.kwargs["max_retries"], 2)
        self.assertEqual(
            self.client.chat.completions.create.call_args.kwargs["max_completion_tokens"], 4096
        )

    def test_response_errors_are_sanitized(self):
        for content in ('{"observations":[],"secret":"private prose"}', '{"observations":[]}'):
            self.client.chat.completions.create.return_value = response(content)
            with self.assertRaises(ShuyiError) as raised:
                self.classifier.classify([make_node(1)])
            self.assertEqual(str(raised.exception), "model_response_invalid")

    def test_refusal_truncation_and_transport_failure_are_sanitized(self):
        self.client.chat.completions.create.return_value = response(None, finish="length")
        with self.assertRaisesRegex(ShuyiError, "model_response_invalid"):
            self.classifier.classify([make_node(1)])
        self.client.chat.completions.create.side_effect = RuntimeError("secret-key private prose")
        with self.assertRaises(ShuyiError) as raised:
            self.classifier.classify([make_node(1)])
        self.assertEqual(str(raised.exception), "model_request_failed")

    def test_missing_credentials_are_deferred_until_classification(self):
        classifier = OpenAIClassifier("https://model.example/v1", "example", "")
        self.assertEqual(
            classifier.provenance, "openai-compatible:https://model.example/v1:example"
        )
        with self.assertRaisesRegex(ShuyiError, "model_config_missing"):
            classifier.classify([make_node(1)])
        self.factory.assert_not_called()
