from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

from shuyi.contracts import ClassificationBatch, LayoutNode, ShuyiError
from shuyi.layout.protocol import (
    MAX_CHARACTERS,
    MAX_OBSERVATIONS,
    SYSTEM_PROMPT,
    request_size,
    request_text,
    strict_json,
    validate_observations,
    validate_usage,
)


class OpenAIClassifier:
    def __init__(self, base_url: str, model: str, api_key: str) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        parsed = urlsplit(base_url)
        safe_base = parsed._replace(
            netloc=parsed.netloc.rsplit("@", 1)[-1], query="", fragment=""
        ).geturl()
        self.provenance = f"openai-compatible:{safe_base}:{model}"

    def classify(self, samples: Sequence[LayoutNode]) -> ClassificationBatch:
        if not self.api_key or not self.model:
            raise ShuyiError("model_config_missing")
        if not samples or len(samples) > MAX_OBSERVATIONS or request_size(samples) > MAX_CHARACTERS:
            raise ShuyiError("model_request_limit")
        if len({node.node_id for node in samples}) != len(samples):
            raise ShuyiError("model_request_invalid")
        from openai import OpenAI

        try:
            with OpenAI(
                base_url=self.base_url, api_key=self.api_key, timeout=60.0, max_retries=2
            ) as client:
                response = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": request_text(samples)},
                    ],
                    response_format={"type": "json_object"},
                    max_completion_tokens=4096,
                )
        except Exception:
            raise ShuyiError("model_request_failed") from None
        try:
            if len(response.choices) != 1 or response.choices[0].finish_reason != "stop":
                raise ShuyiError("model_response_invalid")
            raw = strict_json(response.choices[0].message.content)
            if not isinstance(raw, dict) or set(raw) != {"observations"}:
                raise ShuyiError("model_response_invalid")
            observations = validate_observations(raw["observations"], [n.node_id for n in samples])
            usage = {}
            if response.usage is not None:
                usage = validate_usage(
                    {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens,
                    }
                )
            return ClassificationBatch(observations, usage)
        except ShuyiError:
            raise
        except Exception:
            raise ShuyiError("model_response_invalid") from None
