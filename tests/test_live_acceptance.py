from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


@unittest.skipUnless(os.environ.get("SHUYI_LIVE_ACCEPTANCE") == "1", "live acceptance is opt-in")
class TestLiveAcceptance(unittest.TestCase):
    def test_private_book_real_model_and_credential_free_cached_restyle(self) -> None:
        source_value = os.environ.get("SHUYI_TEST_EPUB")
        if not source_value:
            self.skipTest("SHUYI_TEST_EPUB is not configured")
        source = Path(source_value).expanduser().resolve()
        self.assertTrue(source.is_file(), "Private input does not exist")
        base = Path(os.environ.get("SHUYI_TEST_OUTPUT_DIR", "artifacts/acceptance")).absolute()
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        directory = base / f"{stamp}-{uuid4().hex[:8]}"
        directory.mkdir(parents=True)
        environment = dict(os.environ)
        environment["XDG_CACHE_HOME"] = str(base / "cache")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        config = os.environ.get("SHUYI_TEST_CONFIG")
        ainiee = os.environ.get("SHUYI_TEST_AINIEE_CONFIG")
        if ainiee:
            selected = json.loads(Path(ainiee).expanduser().read_text(encoding="utf-8"))
            platform = selected["platforms"][selected["api_settings"]["active"]]
            environment["SHUYI_AINIEE_API_KEY"] = platform["api_key"].strip()
            model_config = directory / "model.toml"
            model_config.write_text(
                "[model]\nbase_url = "
                + json.dumps(platform["api_url"])
                + "\nname = "
                + json.dumps(platform["model"])
                + '\napi_key_env = "SHUYI_AINIEE_API_KEY"\n',
                encoding="utf-8",
            )
            config = str(model_config)
            del selected, platform
        config_args = ["--config", config] if config else []
        first_path = directory / "styled.epub"
        first = self.run_cli(
            ["style", str(source), "-o", str(first_path), *config_args],
            environment,
            directory / "first",
        )
        self.assertEqual(first["source_sha256"], source_hash)
        self.assertGreater(sum(first["role_counts"].values()), 0)
        self.run_cli(["check", str(first_path)], environment, directory / "check")
        from shuyi.config import load_config

        selected_model = load_config(Path(config) if config else None).model
        environment.pop(selected_model.api_key_env, None)
        environment.pop("OPENAI_API_KEY", None)
        environment.pop("SHUYI_NO_CREDENTIAL", None)
        cached_config = directory / "cached-model.toml"
        cached_config.write_text(
            "[model]\nbase_url = "
            + json.dumps(selected_model.base_url)
            + "\nname = "
            + json.dumps(selected_model.name)
            + '\napi_key_env = "SHUYI_NO_CREDENTIAL"\n',
            encoding="utf-8",
        )
        config_args = ["--config", str(cached_config)]
        second_path = directory / "cached.epub"
        second = self.run_cli(
            ["style", str(source), "-o", str(second_path), *config_args],
            environment,
            directory / "cached",
        )
        self.assertGreater(second["cache_hits"], 0)
        self.assertEqual(first["role_counts"], second["role_counts"])
        self.assertEqual(first["analysis_provenance"], second["analysis_provenance"])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)
        evidence = {
            "input": str(source),
            "source_sha256": source_hash,
            "first": first,
            "cached": second,
            "model_credentials_removed_on_second": True,
        }
        (directory / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(f"Live acceptance evidence: {directory / 'evidence.json'}", flush=True)

    def run_cli(self, arguments: list[str], environment: dict[str, str], log: Path) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "shuyi", *arguments],
            env=environment,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        log.with_suffix(".stdout.json").write_text(result.stdout, encoding="utf-8")
        log.with_suffix(".stderr.log").write_text(result.stderr, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)
