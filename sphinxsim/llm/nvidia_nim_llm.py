"""NVIDIA NIM-backed LLM provider for SPHinXsim config generation."""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from typing import Any, Dict
from urllib import error, request

from pydantic import ValidationError

from sphinxsim.config.schemas import SimulationConfig
from sphinxsim.config.update_patch import UpdatePatch
from sphinxsim.llm.common import (
    BODY_TYPE_RULES,
    apply_explicit_instruction_overrides,
    apply_stl_geometry_overrides,
    apply_shape_rename,
    coerce_simulation_type,
    dict_diff,
    example_config,
    infer_requested_material_type,
    infer_requested_shape_rename,
    infer_requested_simulation_type,
    is_all_plastic_continuum_dict,
    json_safe_errors,
    merge_dicts,
    report_llm_repair,
    sanitize_config_dict,
    strip_code_fences,
    suppress_implicit_plastic_observers,
)


GENERATION_SYSTEM_PROMPT = (
    "You are a simulator configuration generator. "
    "Return ONLY valid JSON in exactly the same structure as 'example_output', "
    "with values adapted for the new description. "
    "Choose the correct simulation type and body/material families for the requested physics. "
    "Return exactly one JSON object and nothing before or after it: no explanation, "
    "no duplicate object, and no markdown. Before responding, verify that the complete "
    "response parses with a strict standard JSON parser, including every required comma, "
    "quote, bracket, and brace. "
)


def build_generation_messages(
    description: str,
    example_cfg: Dict[str, Any] | None,
    *,
    body_type_rules: str = BODY_TYPE_RULES,
) -> list[dict[str, Any]]:
    """Build the exact messages used by production config generation."""
    user: Dict[str, Any] = {"description": description}
    if example_cfg is not None:
        user["example_output"] = example_cfg
    return [
        {"role": "system", "content": GENERATION_SYSTEM_PROMPT + body_type_rules},
        {"role": "user", "content": json.dumps(user)},
    ]


def build_generation_repair_messages(
    description: str,
    candidate: Dict[str, Any],
    validation_errors: list[dict[str, Any]],
    example_cfg: Dict[str, Any] | None,
    *,
    body_type_rules: str = BODY_TYPE_RULES,
) -> list[dict[str, Any]]:
    """Build the exact messages used by production's one repair attempt."""
    retry_system = (
        "You are repairing a newly generated simulator config that failed schema validation. "
        f"Continue to satisfy this original description: \"{description}\". "
        "Return ONLY full valid JSON. Preserve valid user-requested values and structure. "
        "Fix all reported validation errors. "
    ) + body_type_rules
    retry_user: Dict[str, Any] = {
        "description": description,
        "candidate_config": candidate,
        "validation_errors": validation_errors,
    }
    if example_cfg is not None:
        retry_user["example_output"] = example_cfg
    return [
        {"role": "system", "content": retry_system},
        {"role": "user", "content": json.dumps(retry_user)},
    ]


@dataclass
class NvidiaNIMLLM:
    """Generate and update SimulationConfig using NVIDIA NIM's OpenAI-compatible API."""

    base_url: str = "https://integrate.api.nvidia.com/v1"
    model: str = "z-ai/glm-5.2"
    fallback_models: tuple[str, ...] = ()
    api_key: str | None = None
    timeout: float = 60.0
    _BODY_TYPE_RULES: str = BODY_TYPE_RULES

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ValueError(
                "NVIDIA NIM API key is required. Set NVIDIA_NIM_API_KEY or NVIDIA_API_KEY."
            )
        self.model = self.model.strip()
        self.fallback_models = tuple(
            m.strip() for m in self.fallback_models if isinstance(m, str) and m.strip() and m.strip() != self.model
        )

    def _endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @staticmethod
    def _extract_error_detail(raw_detail: str) -> str:
        text = (raw_detail or "").strip()
        if not text:
            return ""
        try:
            parsed = json.loads(text)
        except Exception:
            return text
        if isinstance(parsed, dict):
            detail = parsed.get("detail")
            if isinstance(detail, str):
                return detail
        return text

    @staticmethod
    def _is_degraded_model_error(http_code: int, detail: str) -> bool:
        if http_code != 400:
            return False
        normalized = detail.lower()
        return "degraded" in normalized and "cannot be invoked" in normalized

    def _post_chat(self, *, messages: list[dict[str, Any]], temperature: float = 0.0) -> str:
        model_candidates = (self.model,) + self.fallback_models
        last_http_error: tuple[int, str] | None = None

        for model_name in model_candidates:
            payload = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "top_p": 1,
                "stream": False,
            }
            body = json.dumps(payload).encode("utf-8")
            req = request.Request(
                self._endpoint(),
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )

            try:
                with request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                break
            except error.HTTPError as exc:
                detail_raw = exc.read().decode("utf-8", errors="ignore")
                detail = self._extract_error_detail(detail_raw)
                last_http_error = (exc.code, detail)
                if self._is_degraded_model_error(exc.code, detail) and model_name != model_candidates[-1]:
                    continue
                if self._is_degraded_model_error(exc.code, detail):
                    raise RuntimeError(
                        "NVIDIA NIM model is currently unavailable (degraded). "
                        "Set NVIDIA_NIM_MODEL to an available model and optionally set "
                        "NVIDIA_NIM_FALLBACK_MODELS with comma-separated alternatives. "
                        f"Detail: {detail}"
                    ) from exc
                raise RuntimeError(f"NVIDIA NIM request failed with HTTP {exc.code}: {detail}") from exc
            except error.URLError as exc:
                raise RuntimeError(
                    "Failed to contact NVIDIA NIM. Ensure NVIDIA_NIM_BASE_URL is correct and network is available."
                ) from exc
            except TimeoutError as exc:
                raise RuntimeError(
                    "NVIDIA NIM request timed out. Increase NVIDIA_NIM_TIMEOUT or use a smaller prompt/model."
                ) from exc
            except OSError as exc:
                raise RuntimeError("Failed during NVIDIA NIM request.") from exc
        else:
            if last_http_error is not None:
                code, detail = last_http_error
                raise RuntimeError(f"NVIDIA NIM request failed with HTTP {code}: {detail}")
            raise RuntimeError("NVIDIA NIM request failed before sending request.")

        data = json.loads(raw)
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("NVIDIA NIM returned no choices")
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
            content = "".join(parts)

        if not isinstance(content, str) or not content.strip():
            raise ValueError("NVIDIA NIM returned an empty response")

        return content.strip()

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        return strip_code_fences(text)

    @staticmethod
    def _dict_diff(base: Any, updated: Any) -> Any:
        return dict_diff(base, updated)

    def _load_json_content(self, content: str) -> Dict[str, Any]:
        cleaned = self._strip_code_fences(content).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            if exc.msg != "Extra data":
                raise
            # Some chat models append a short explanation after an otherwise
            # complete response. Accept the first dictionary only when the tail
            # is prose; a second JSON value is ambiguous and must be regenerated.
            parsed, end = json.JSONDecoder().raw_decode(cleaned)
            trailing = cleaned[end:].strip()
            if (
                not isinstance(parsed, dict)
                or not trailing
                or trailing.startswith(("{", "["))
            ):
                raise
            warnings.warn(
                "Ignored trailing content after the first complete generated JSON object.",
                RuntimeWarning,
                stacklevel=2,
            )
            return parsed

    @staticmethod
    def _example_config(description: str) -> Dict[str, Any]:
        return example_config(description)

    @staticmethod
    def _merge_dicts(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        return merge_dicts(base, updates)

    @staticmethod
    def _sanitize_config_dict(cfg: Dict[str, Any]) -> Dict[str, Any]:
        return sanitize_config_dict(cfg)

    @staticmethod
    def _apply_stl_geometry_overrides(cfg: Dict[str, Any], description: str) -> Dict[str, Any]:
        return apply_stl_geometry_overrides(cfg, description)

    @staticmethod
    def _infer_requested_simulation_type(description: str) -> str | None:
        return infer_requested_simulation_type(description)

    @staticmethod
    def _infer_requested_material_type(description: str) -> str | None:
        return infer_requested_material_type(description)

    @staticmethod
    def _coerce_simulation_type(
        existing: Dict[str, Any],
        target_type: str,
        material_type: str | None = None,
    ) -> Dict[str, Any]:
        return coerce_simulation_type(existing, target_type, material_type=material_type)

    @staticmethod
    def _infer_requested_shape_rename(description: str) -> tuple[str, str] | None:
        return infer_requested_shape_rename(description)

    @staticmethod
    def _apply_shape_rename(config_dict: Dict[str, Any], old_name: str, new_name: str) -> Dict[str, Any]:
        return apply_shape_rename(config_dict, old_name, new_name)

    def generate(self, description: str) -> SimulationConfig:
        if not description or not description.strip():
            raise ValueError("description must not be empty")

        example_cfg = self._example_config(description)

        content = self._post_chat(
            messages=build_generation_messages(
                description,
                example_cfg,
                body_type_rules=self._BODY_TYPE_RULES,
            ),
            temperature=0.0,
        )

        data = self._load_json_content(content)
        if not isinstance(data, dict):
            raise ValueError("NVIDIA NIM returned an invalid generation response")

        merged = self._merge_dicts(example_cfg, data)
        merged = self._apply_stl_geometry_overrides(merged, description)
        merged = apply_explicit_instruction_overrides(merged, description)
        merged = self._sanitize_config_dict(merged)
        merged = suppress_implicit_plastic_observers(merged, description)
        try:
            return SimulationConfig(**merged)
        except ValidationError as exc:
            safe_validation_errors = json_safe_errors(exc.errors())
            retry_content = self._post_chat(
                messages=build_generation_repair_messages(
                    description,
                    merged,
                    safe_validation_errors,
                    example_cfg,
                    body_type_rules=self._BODY_TYPE_RULES,
                ),
                temperature=0.0,
            )
            retry_data = self._load_json_content(retry_content)
            if isinstance(retry_data, dict):
                retried = self._merge_dicts(example_cfg, retry_data)
                retried = self._apply_stl_geometry_overrides(retried, description)
                retried = apply_explicit_instruction_overrides(retried, description)
                retried = self._sanitize_config_dict(retried)
                retried = suppress_implicit_plastic_observers(retried, description)
                try:
                    validated = SimulationConfig(**retried)
                    report_llm_repair(merged, retried)
                    return validated
                except ValidationError:
                    pass

            # The single LLM repair attempt also failed schema validation.
            # Restore the validated example structure as the final deterministic fallback.
            repaired = (
                example_cfg
                if is_all_plastic_continuum_dict(example_cfg)
                else self._merge_dicts(merged, example_cfg)
            )
            repaired = self._apply_stl_geometry_overrides(repaired, description)
            repaired = apply_explicit_instruction_overrides(repaired, description)
            repaired = self._sanitize_config_dict(repaired)
            repaired = suppress_implicit_plastic_observers(repaired, description)
            return SimulationConfig(**repaired)

    def update(self, existing: SimulationConfig, description: str) -> SimulationConfig:
        if not description or not description.strip():
            raise ValueError("description must not be empty")

        existing_dict = existing.model_dump(exclude_none=True)
        existing_json = json.dumps(existing_dict)
        system = (
            f"You revise simulator configurations. "
            f"The update instruction is: \"{description}\". "
            f"Apply it to the JSON config the user provides and return ONLY the full updated JSON "
            f"in the same structure, with only the requested changes applied. "
            f"Preserve all existing fields unless the instruction explicitly changes them. "
            f"Do not remove arrays like geometries.shapes or body definitions. "
        ) + self._BODY_TYPE_RULES

        content = self._post_chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": existing_json},
            ],
            temperature=0.0,
        )

        data = self._load_json_content(content)
        if not isinstance(data, dict):
            raise ValueError("NVIDIA NIM returned an invalid update response")

        merged = self._merge_dicts(existing_dict, data)
        requested_type = self._infer_requested_simulation_type(description)
        requested_material = self._infer_requested_material_type(description)
        requested_shape_rename = self._infer_requested_shape_rename(description)
        if requested_type is not None:
            merged = self._coerce_simulation_type(merged, requested_type, requested_material)
        merged = self._apply_stl_geometry_overrides(merged, description)
        if requested_shape_rename is not None:
            merged = self._apply_shape_rename(merged, requested_shape_rename[0], requested_shape_rename[1])
        merged = self._sanitize_config_dict(merged)
        try:
            return SimulationConfig(**merged)
        except ValidationError as exc:
            safe_validation_errors = json_safe_errors(exc.errors())
            retry_system = (
                "You are repairing a simulator config update that failed schema validation. "
                f"Apply this instruction: \"{description}\". "
                "Return ONLY full valid JSON. Preserve existing structure and non-target fields. "
                "Fix all reported validation errors. "
            ) + self._BODY_TYPE_RULES
            retry_user = {
                "instruction": description,
                "existing_config": existing_dict,
                "candidate_config": merged,
                "validation_errors": safe_validation_errors,
                "example_output": self._example_config(description),
            }
            retry_content = self._post_chat(
                messages=[
                    {"role": "system", "content": retry_system},
                    {"role": "user", "content": json.dumps(retry_user)},
                ],
                temperature=0.0,
            )
            retry_data = self._load_json_content(retry_content)
            if isinstance(retry_data, dict):
                retried = self._merge_dicts(existing_dict, retry_data)
                if requested_type is not None:
                    retried = self._coerce_simulation_type(retried, requested_type, requested_material)
                retried = self._apply_stl_geometry_overrides(retried, description)
                if requested_shape_rename is not None:
                    retried = self._apply_shape_rename(retried, requested_shape_rename[0], requested_shape_rename[1])
                retried = self._sanitize_config_dict(retried)
                try:
                    return SimulationConfig(**retried)
                except ValidationError:
                    pass

            if requested_type is not None:
                coerced = self._coerce_simulation_type(existing_dict, requested_type, requested_material)
                coerced = self._apply_stl_geometry_overrides(coerced, description)
                if requested_shape_rename is not None:
                    coerced = self._apply_shape_rename(coerced, requested_shape_rename[0], requested_shape_rename[1])
                coerced = self._sanitize_config_dict(coerced)
                return SimulationConfig(**coerced)

            if requested_shape_rename is not None:
                renamed = self._apply_shape_rename(existing_dict, requested_shape_rename[0], requested_shape_rename[1])
                renamed = self._sanitize_config_dict(renamed)
                return SimulationConfig(**renamed)

            raise

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

        content = self._post_chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
            temperature=0.0,
        )
        answer = self._strip_code_fences(content).strip()
        if not answer:
            raise ValueError("NVIDIA NIM returned an empty exploration answer")
        return answer
