# sphinxsim/llm/openai_llm.py
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from pydantic import ValidationError

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.config.update_patch import UpdatePatch
from sphinxsim.llm.common import (
    BODY_TYPE_RULES,
    apply_explicit_instruction_overrides,
    apply_stl_geometry_overrides,
    dict_diff,
    json_safe_errors,
    report_llm_repair,
    strip_code_fences,
    suppress_implicit_plastic_observers,
)

# OpenAI SDK is optional until this provider is selected.
try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - exercised through provider construction
    OpenAI = None  # type: ignore[assignment]


@dataclass
class OpenAILLM:
    model: str = "gpt-4.1-mini"  # pick what you want
    api_key: Optional[str] = None

    def __post_init__(self) -> None:
        if OpenAI is None:
            raise RuntimeError(
                "The OpenAI provider requires the optional 'openai' package."
            )
        self.client = OpenAI(api_key=self.api_key)

    def generate(self, description: str) -> SimulationConfig:
        if not description or not description.strip():
            raise ValueError("description must not be empty")

        # If SimulationConfig is Pydantic v2, you can use model_json_schema()
        schema = SimulationConfig.model_json_schema()

        system = (
            "You are a simulator configuration generator. "
            "Return ONLY valid JSON that conforms to the provided JSON Schema. "
            "For granular soil, landslide, slope, column collapse, Drucker-Prager, "
            "friction angle, cohesion, or dilatancy requests, use simulation_type "
            "'continuum_dynamics' with a continuum_bodies material.type of "
            "'plastic_continuum'. plastic_continuum requires density, youngs_modulus, "
            "poisson_ratio, and friction_angle; sound_speed, cohesion, and dilatancy_angle "
            "are optional. "
            "Do not include markdown, comments, or extra keys."
        ) + BODY_TYPE_RULES

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
        data: Dict[str, Any] = json.loads(strip_code_fences(content))
        data = apply_stl_geometry_overrides(data, description)
        data = apply_explicit_instruction_overrides(data, description)
        data = suppress_implicit_plastic_observers(data, description)

        try:
            return SimulationConfig(**data)
        except ValidationError as exc:
            retry_user = {
                "description": description,
                "candidate_config": data,
                "validation_errors": json_safe_errors(exc.errors()),
                "json_schema": schema,
            }
            retry_resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Repair the generated simulator config using the reported schema or "
                            "physical validation errors. Return ONLY full valid JSON and preserve "
                            "all valid user-requested values. "
                        )
                        + BODY_TYPE_RULES,
                    },
                    {"role": "user", "content": json.dumps(retry_user)},
                ],
                temperature=0,
            )
            retry_content = retry_resp.choices[0].message.content or ""
            retry_data: Dict[str, Any] = json.loads(strip_code_fences(retry_content))
            retry_data = apply_stl_geometry_overrides(retry_data, description)
            retry_data = apply_explicit_instruction_overrides(retry_data, description)
            retry_data = suppress_implicit_plastic_observers(retry_data, description)
            validated = SimulationConfig(**retry_data)
            report_llm_repair(data, retry_data)
            return validated

    def update(self, existing: SimulationConfig, description: str) -> SimulationConfig:
        if not description or not description.strip():
            raise ValueError("description must not be empty")

        schema = SimulationConfig.model_json_schema()

        system = (
            "You revise simulator configurations. "
            "Given an existing config and an update instruction, return ONLY the full updated JSON "
            "that conforms to the provided JSON Schema. "
            "For granular soil, landslide, slope, column collapse, Drucker-Prager, "
            "friction angle, cohesion, or dilatancy requests, keep or use simulation_type "
            "'continuum_dynamics' with a continuum_bodies material.type of "
            "'plastic_continuum'. "
            "Do not include markdown, comments, or extra keys."
        )

        user = {
            "instruction": description,
            "existing_config": existing.model_dump(),
            "json_schema": schema,
        }

        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
            temperature=0,
        )

        content = resp.choices[0].message.content or ""
        data: Dict[str, Any] = json.loads(strip_code_fences(content))
        return SimulationConfig(**data)

    @staticmethod
    def _dict_diff(base: Any, updated: Any) -> Any:
        return dict_diff(base, updated)

    def update_patch(self, existing: SimulationConfig, description: str, strict: bool = True) -> Dict[str, Any]:
        updated = self.update(existing, description)
        base = existing.model_dump(exclude_none=True)
        target = updated.model_dump(exclude_none=True)
        delta = self._dict_diff(base, target) or {}
        patch = UpdatePatch(
            strict=strict,
            operations=[
                {
                    "op": "merge_object",
                    "path": "",
                    "value": delta,
                }
            ],
        )
        return patch.model_dump(exclude_none=True)

    def explore(self, question: str, context: str | None = None) -> str:
        if not question or not question.strip():
            raise ValueError("question must not be empty")

        system = (
            "You explain SPHinXsim schema and simulator functionality. "
            "Be accurate, concise, and practical."
        )
        user = {
            "question": question,
            "context": context or "",
        }

        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
            temperature=0,
        )

        content = resp.choices[0].message.content or ""
        answer = content.strip()
        if not answer:
            raise ValueError("OpenAI returned an empty exploration answer")
        return answer
