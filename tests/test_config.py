from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shuyi.config import ConfiguredClassifier, ModelConfig, load_config
from shuyi.contracts import ShuyiError
from tests.test_layout import make_node


class TestConfig(unittest.TestCase):
    def test_toml_overrides_environment_and_unknown_fields_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[model]\nname="chosen"\nbase_url="http://localhost:8080/v1"\n')
            with patch.dict(
                os.environ, {"OPENAI_MODEL": "env-model", "OPENAI_BASE_URL": "https://example.com"}
            ):
                config = load_config(path)
            self.assertEqual(config.model.name, "chosen")
            self.assertEqual(config.model.base_url, "http://localhost:8080/v1")
            for content in (
                "extra=true",
                '[model]\napi_key=""',
                "[model]\napi_key=123",
                '[model]\napi_key="secret"\napi_key_env="OPENAI_API_KEY"',
                '[model]\nbase_url="file:///tmp"',
                '[model]\napi_key_env="not a variable"',
                "[model]\nname=123",
            ):
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaisesRegex(ShuyiError, "config_invalid"):
                        load_config(path)

    def test_missing_explicit_config_fails_but_absent_default_can_use_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ShuyiError, "config_missing"):
                load_config(Path(directory) / "missing.toml")
            with patch.dict(
                os.environ,
                {"XDG_CONFIG_HOME": directory, "OPENAI_MODEL": "environment-model"},
                clear=True,
            ):
                self.assertEqual(load_config().model.name, "environment-model")

    def test_literal_key_is_accepted_without_environment_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[model]\nname="chosen"\napi_key="local-test-credential"\n')
            with patch.dict(os.environ, {}, clear=True):
                config = load_config(path)
            self.assertEqual(config.model.api_key.get_secret_value(), "local-test-credential")
            self.assertNotIn("local-test-credential", repr(config))
            self.assertNotIn("local-test-credential", config.model_dump_json())

    def test_credential_validation_errors_do_not_expose_literal_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                '[model]\napi_key="private-test-credential"\napi_key_env="OPENAI_API_KEY"\n'
            )
            with self.assertRaisesRegex(ShuyiError, "^config_invalid$") as raised:
                load_config(path)
            self.assertNotIn("private-test-credential", str(raised.exception))

    def test_literal_key_reaches_transport_and_sanitizes_provider_errors(self) -> None:
        classifier = ConfiguredClassifier(
            ModelConfig(name="chosen", api_key="private-test-credential")
        )
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("openai.OpenAI", side_effect=RuntimeError("private-test-credential")),
            self.assertRaisesRegex(ShuyiError, "^model_request_failed$") as raised,
        ):
            classifier.classify([make_node(1)])
        self.assertNotIn("private-test-credential", str(raised.exception))
