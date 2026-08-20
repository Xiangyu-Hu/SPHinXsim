"""Ollama-backed LLM provider for SPHinXsim config generation."""

from __future__ import annotations

import json
import re
import time
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
    coerce_simulation_type,
    dict_diff,
    example_config,
    infer_requested_material_type,
    infer_requested_simulation_type,
    is_all_plastic_continuum_dict,
    json_safe_errors,
    merge_dicts,
    report_llm_repair,
    sanitize_config_dict,
    strip_code_fences,
    suppress_implicit_plastic_observers,
)


@dataclass
class OllamaLLM:
    """Generate and update SimulationConfig using a local Ollama server."""

    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:3b"
    timeout: float = 60.0

    def _endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/chat"

    @staticmethod
    def _repair_json_text(text: str) -> str:
        repaired = text.strip()

        # Some local models return prose before/after the object even when
        # format=json is requested. Keep the outermost JSON-looking payload.
        first_obj = repaired.find("{")
        last_obj = repaired.rfind("}")
        first_arr = repaired.find("[")
        last_arr = repaired.rfind("]")
        if first_obj != -1 and last_obj > first_obj:
            repaired = repaired[first_obj : last_obj + 1]
        elif first_arr != -1 and last_arr > first_arr:
            repaired = repaired[first_arr : last_arr + 1]

        # Common Ollama small-model glitches: missing comma before the next
        # object key and trailing comma before a closing brace/bracket.
        repaired = re.sub(r"([}\]])(\s*\n\s*\")", r"\1,\2", repaired)
        repaired = re.sub(
            r"((?:\"(?:[^\"\\]|\\.)*\")|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null)(\s*\n\s*\")",
            r"\1,\2",
            repaired,
        )
        repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
        return repaired

    @staticmethod
    def _loads_json_content(text: str) -> Any:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            repaired = OllamaLLM._repair_json_text(text)
            if repaired == text:
                raise
            return json.loads(repaired)

    @staticmethod
    def _fallback_json_from_messages(messages: list) -> Dict[str, Any] | None:
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                example_output = payload.get("example_output")
                if isinstance(example_output, dict):
                    return example_output
                existing_config = payload.get("existing_config")
                if isinstance(existing_config, dict):
                    return existing_config
                if "simulation_type" in payload:
                    return payload
        return None

    def _post_chat(self, *, messages: list, format_json: bool = True) -> Any:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": messages,
        }
        if format_json:
            payload["format"] = "json"

        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self._endpoint(),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        raw = ""
        for attempt in range(2):
            try:
                with request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                break
            except error.URLError as exc:
                raise RuntimeError(
                    "Failed to contact Ollama server. "
                    "Ensure Ollama is running and OLLAMA_BASE_URL is correct."
                ) from exc
            except TimeoutError as exc:
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise RuntimeError(
                    "Ollama request timed out. Increase OLLAMA_TIMEOUT or use a smaller model."
                ) from exc
            except OSError as exc:
                # socket.py can raise raw TimeoutError/OSError on read timeout
                if "timed out" in str(exc).lower() and attempt == 0:
                    time.sleep(1)
                    continue
                raise RuntimeError(
                    "Failed during Ollama request. Ensure Ollama is reachable and responsive."
                ) from exc

        data = json.loads(raw)
        message = data.get("message") or {}
        content = message.get("content", "")

        if isinstance(content, dict):
            return content

        if not isinstance(content, str) or not content.strip():
            raise ValueError("Ollama returned an empty response")

        text = content.strip()
        text = strip_code_fences(text)

        if not format_json:
            return text

        try:
            return self._loads_json_content(text)
        except json.JSONDecodeError as exc:
            fallback = self._fallback_json_from_messages(messages)
            if fallback is not None:
                return fallback
            raise ValueError("Ollama returned invalid JSON") from exc

    @staticmethod
    def _example_config(description: str) -> Dict[str, Any]:
        return example_config(description)

    @staticmethod
    def _merge_dicts(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        return merge_dicts(base, updates)

    @staticmethod
    def _dict_diff(base: Any, updated: Any) -> Any:
        return dict_diff(base, updated)

    @staticmethod
    def _apply_explicit_instruction_overrides(cfg: Dict[str, Any], description: str) -> Dict[str, Any]:
        return apply_explicit_instruction_overrides(cfg, description)

    @staticmethod
    def _apply_stl_geometry_overrides(cfg: Dict[str, Any], description: str) -> Dict[str, Any]:
        return apply_stl_geometry_overrides(cfg, description)

    @staticmethod
    def _sanitize_config_dict(cfg: Dict[str, Any]) -> Dict[str, Any]:
        return sanitize_config_dict(cfg)

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

    _BODY_TYPE_RULES: str = BODY_TYPE_RULES

    def generate(self, description: str) -> SimulationConfig:
        if not description or not description.strip():
            raise ValueError("description must not be empty")

        system = (
            "You are a simulator configuration generator. "
            "Return ONLY valid JSON in exactly the same structure as 'example_output', "
            "with values adapted for the new description. "
            "Choose the correct simulation type and body/material families for the requested physics. "
        ) + self._BODY_TYPE_RULES
        example_cfg = self._example_config(description)
        user = {
            "description": description,
            "example_output": example_cfg,
        }

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user)},
        ]
        data = self._post_chat(messages=messages)
        if not isinstance(data, dict):
            raise ValueError("Ollama returned an invalid generation response")
        merged = self._merge_dicts(example_cfg, data)
        merged = self._apply_stl_geometry_overrides(merged, description)
        merged = self._apply_explicit_instruction_overrides(merged, description)
        merged = self._sanitize_config_dict(merged)
        merged = suppress_implicit_plastic_observers(merged, description)
        try:
            return SimulationConfig(**merged)
        except ValidationError as exc:
            retry_system = (
                "You are repairing a newly generated simulator config that failed schema or "
                "physical validation. Return ONLY the full corrected JSON. Preserve valid "
                "user-requested values and fix every reported error. "
            ) + self._BODY_TYPE_RULES
            retry_user = {
                "description": description,
                "candidate_config": merged,
                "validation_errors": json_safe_errors(exc.errors()),
                "example_output": example_cfg,
            }
            retry_data = self._post_chat(
                messages=[
                    {"role": "system", "content": retry_system},
                    {"role": "user", "content": json.dumps(retry_user)},
                ]
            )
            if isinstance(retry_data, dict):
                retried = self._merge_dicts(example_cfg, retry_data)
                retried = self._apply_stl_geometry_overrides(retried, description)
                retried = self._apply_explicit_instruction_overrides(retried, description)
                retried = self._sanitize_config_dict(retried)
                retried = suppress_implicit_plastic_observers(retried, description)
                try:
                    validated = SimulationConfig(**retried)
                    report_llm_repair(merged, retried)
                    return validated
                except ValidationError:
                    pass

            repaired = (
                example_cfg
                if is_all_plastic_continuum_dict(example_cfg)
                else self._merge_dicts(merged, example_cfg)
            )
            repaired = self._apply_stl_geometry_overrides(repaired, description)
            repaired = self._apply_explicit_instruction_overrides(repaired, description)
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

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": existing_json},
        ]
        data = self._post_chat(messages=messages)
        if not isinstance(data, dict):
            raise ValueError("Ollama returned an invalid update response")
        merged = self._merge_dicts(existing_dict, data)
        patch_data = None
        if merged == existing_dict:
            patch_system = (
                f"You revise simulator configurations. The instruction is: \"{description}\". "
                f"Return ONLY a minimal JSON patch object containing changed fields. "
                f"Examples: {{\"solver_parameters\": {{\"end_time\": 2.0}}}} or "
                f"{{\"geometries\": {{\"global_resolution\": {{\"particle_spacing\": 0.005}}}}}}."
            )
            patch_user = {
                "instruction": description,
                "existing_config": existing_dict,
            }
            patch_data = self._post_chat(
                messages=[
                    {"role": "system", "content": patch_system},
                    {"role": "user", "content": json.dumps(patch_user)},
                ]
            )
        if isinstance(patch_data, dict):
            merged = self._merge_dicts(existing_dict, patch_data)
        merged = self._apply_explicit_instruction_overrides(merged, description)
        merged = self._apply_stl_geometry_overrides(merged, description)
        requested_type = self._infer_requested_simulation_type(description)
        requested_material = self._infer_requested_material_type(description)
        if requested_type is not None:
            merged = self._coerce_simulation_type(merged, requested_type, requested_material)
            merged = self._apply_stl_geometry_overrides(merged, description)
        merged = self._sanitize_config_dict(merged)
        try:
            return SimulationConfig(**merged)
        except Exception:
            repaired = self._merge_dicts(merged, existing_dict)
            if requested_type is not None:
                repaired = self._coerce_simulation_type(repaired, requested_type, requested_material)
            repaired = self._apply_stl_geometry_overrides(repaired, description)
            repaired = self._sanitize_config_dict(repaired)
            return SimulationConfig(**repaired)

    def update_patch(self, existing: SimulationConfig, description: str, strict: bool = True) -> Dict[str, Any]:
        if not description or not description.strip():
            raise ValueError("description must not be empty")

        existing_dict = existing.model_dump(exclude_none=True)
        system = (
            "You generate operation-based JSON patches for simulator configs. "
            "Return ONLY JSON in this shape: "
            "{\"schema_version\":\"1.0\",\"strict\":true|false,\"operations\":[...]}. "
            "Allowed operations: set_value(path,value), merge_object(path,value), "
            "append_item(path,value), upsert_item(path,match,value,on_match,on_missing), "
            "rename_item_key(path,match,key,new_value). "
            "Do not use delete/remove operations."
        )
        user = {
            "instruction": description,
            "existing_config": existing_dict,
            "strict": strict,
        }

        data = self._post_chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ]
        )
        if isinstance(data, dict):
            try:
                patch = UpdatePatch.model_validate(data)
                patch.strict = strict
                return patch.model_dump(exclude_none=True)
            except Exception:
                pass

        updated = self.update(existing, description)
        target = updated.model_dump(exclude_none=True)
        delta = self._dict_diff(existing_dict, target) or {}
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
            "Answer in plain text. Be concise, accurate, and practical."
        )
        user = {
            "question": question,
            "context": context or "",
        }

        answer = self._post_chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
            format_json=False,
        )
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Ollama returned an invalid exploration answer")
        return answer.strip()
