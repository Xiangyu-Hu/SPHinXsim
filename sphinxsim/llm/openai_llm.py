# sphinxsim/llm/openai_llm.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from sphinxsim.config.schemas import SimulationConfig

# OpenAI SDK (new-style)
from openai import OpenAI


@dataclass
class OpenAILLM:
    model: str = "gpt-4.1-mini"  # pick what you want
    api_key: Optional[str] = None

    def __post_init__(self) -> None:
        self.client = OpenAI(api_key=self.api_key)

    def generate(self, description: str) -> SimulationConfig:
        if not description or not description.strip():
            raise ValueError("description must not be empty")

        # If SimulationConfig is Pydantic v2, you can use model_json_schema()
        schema = SimulationConfig.model_json_schema()

        system = (
            "You are a simulator configuration generator. "
            "Return ONLY valid JSON that conforms to the provided JSON Schema. "
            "Do not include markdown, comments, or extra keys."
        )

        user = {
            "description": description,
            "json_schema": schema,
        }

        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
            temperature=0,  # deterministic-ish
        )

        content = resp.choices[0].message.content or ""
        data: Dict[str, Any] = json.loads(content)

        # Final safety: schema validation on your side
        return SimulationConfig(**data)
    