from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from shuyi.contracts import ClassificationBatch, LayoutNode, ShuyiError


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    base_url: StrictStr = "https://api.openai.com/v1"
    name: StrictStr = ""
    api_key_env: StrictStr = "OPENAI_API_KEY"
    api_key: SecretStr | None = Field(default=None, repr=False)

    @field_validator("api_key")
    @classmethod
    def validate_literal_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        key = value.get_secret_value().strip()
        if not key:
            raise ValueError("empty API key")
        return SecretStr(key)

    @model_validator(mode="after")
    def validate_key_source(self) -> ModelConfig:
        if self.api_key is not None and "api_key_env" in self.model_fields_set:
            raise ValueError("choose one API key source")
        return self

    @field_validator("base_url")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("invalid endpoint")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("invalid endpoint")
        return value.rstrip("/")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if value and not value.strip():
            raise ValueError("empty model")
        return value.strip()

    @field_validator("api_key_env")
    @classmethod
    def validate_key_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("invalid environment variable")
        return value


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model: ModelConfig


def default_config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "shuyi/config.toml"


def default_cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "shuyi"


def load_config(path: Path | None = None) -> Config:
    selected = path.expanduser() if path is not None else default_config_path()
    raw: dict = {}
    try:
        if selected.exists():
            raw = tomllib.loads(selected.read_text(encoding="utf-8"))
        elif path is not None:
            raise ShuyiError("config_missing")
        if not isinstance(raw, dict) or set(raw) - {"model"}:
            raise ShuyiError("config_invalid")
        supplied = raw.get("model", {})
        if not isinstance(supplied, dict):
            raise ShuyiError("config_invalid")
        if "name" in supplied and (
            not isinstance(supplied["name"], str) or not supplied["name"].strip()
        ):
            raise ShuyiError("config_invalid")
        model = {
            "base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            "name": os.environ.get("OPENAI_MODEL", ""),
            **supplied,
        }
        return Config(model=ModelConfig.model_validate(model))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValidationError, ValueError) as error:
        if isinstance(error, ShuyiError):
            raise
        raise ShuyiError("config_invalid") from None


class ConfiguredClassifier:
    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self.provenance = f"{config.base_url}/{config.name or '<unconfigured>'}"
        self._client = None

    def classify(self, samples: Sequence[LayoutNode]) -> ClassificationBatch:
        from shuyi.layout.model import OpenAIClassifier

        if self._client is None:
            key = (
                self.config.api_key.get_secret_value()
                if self.config.api_key is not None
                else os.environ.get(self.config.api_key_env, "").strip()
            )
            if not key or not self.config.name:
                raise ShuyiError("model_config_missing")
            self._client = OpenAIClassifier(
                base_url=self.config.base_url, model=self.config.name, api_key=key
            )
        return self._client.classify(samples)


def configured_classifier(config: Config) -> ConfiguredClassifier:
    return ConfiguredClassifier(config.model)
