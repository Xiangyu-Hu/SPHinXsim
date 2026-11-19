"""Tests for deterministic English natural-language intent extraction."""

from sphinxsim.agent import RuleBasedIntentResolver, TaskIntentType


def test_create_and_run_intent() -> None:
    intent = RuleBasedIntentResolver().resolve(
        "Generate and run a 2D water dam break simulation."
    )
    assert intent.intent_type == TaskIntentType.CREATE_CASE
    assert intent.should_run is True


def test_generate_only_intent() -> None:
    intent = RuleBasedIntentResolver().resolve(
        "Generate a 3D landslide JSON file, but do not run it."
    )
    assert intent.intent_type == TaskIntentType.CREATE_CASE
    assert intent.should_run is False


def test_update_existing_json_and_run_intent() -> None:
    intent = RuleBasedIntentResolver().resolve(
        "Update cases/base.json to use an end time of 5 seconds, then run it."
    )
    assert intent.intent_type == TaskIntentType.UPDATE_CASE
    assert intent.existing_config_path == "cases/base.json"
    assert intent.should_run is True


def test_landslide_metrics_request_implies_run_and_analysis() -> None:
    intent = RuleBasedIntentResolver().resolve(
        "Simulate a 3D landslide and calculate runout distance and deposit height."
    )
    assert intent.should_run is True
    assert intent.should_analyze is True
    assert intent.requested_metrics == ["runout_distance", "deposit_height"]


def test_update_without_json_requires_clarification() -> None:
    intent = RuleBasedIntentResolver().resolve(
        "Change the simulation end time to 5 seconds."
    )
    assert intent.intent_type == TaskIntentType.CLARIFY
    assert intent.clarification_questions


def test_unknown_request_requires_clarification() -> None:
    intent = RuleBasedIntentResolver().resolve("Help me with this project.")
    assert intent.intent_type == TaskIntentType.CLARIFY
