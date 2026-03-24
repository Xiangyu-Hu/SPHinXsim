import sys
import types

from sphinxsim.llm import get_llm
from sphinxsim.llm.mock_llm import MockLLM


def test_default_provider_is_mock(monkeypatch):
    monkeypatch.delenv("SPHINXSIM_LLM_PROVIDER", raising=False)
    llm = get_llm()
    assert isinstance(llm, MockLLM)


def test_unknown_provider_falls_back_to_mock(monkeypatch):
    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "unknown")
    llm = get_llm()
    assert isinstance(llm, MockLLM)


def test_copilot_provider_uses_expected_env(monkeypatch):
    class DummyCopilotLLM:
        def __init__(self, model=None, api_key=None, base_url=None):
            self.model = model
            self.api_key = api_key
            self.base_url = base_url

    fake_module = types.ModuleType("sphinxsim.llm.copilot_llm")
    fake_module.CopilotLLM = DummyCopilotLLM
    monkeypatch.setitem(sys.modules, "sphinxsim.llm.copilot_llm", fake_module)

    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "copilot")
    monkeypatch.setenv("COPILOT_MODEL", "gpt-4o")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token")
    monkeypatch.setenv("GITHUB_MODELS_BASE_URL", "https://models.example.local")
    monkeypatch.delenv("COPILOT_API_KEY", raising=False)
    monkeypatch.delenv("COPILOT_BASE_URL", raising=False)

    llm = get_llm()
    assert llm.__class__.__name__ == "DummyCopilotLLM"
    assert llm.model == "gpt-4o"
    assert llm.api_key == "gh-token"
    assert llm.base_url == "https://models.example.local"


def test_copilot_alias_github_models(monkeypatch):
    class DummyCopilotLLM:
        def __init__(self, model=None, api_key=None, base_url=None):
            self.model = model
            self.api_key = api_key
            self.base_url = base_url

    fake_module = types.ModuleType("sphinxsim.llm.copilot_llm")
    fake_module.CopilotLLM = DummyCopilotLLM
    monkeypatch.setitem(sys.modules, "sphinxsim.llm.copilot_llm", fake_module)

    monkeypatch.setenv("SPHINXSIM_LLM_PROVIDER", "github-models")
    monkeypatch.setenv("COPILOT_API_KEY", "copilot-key")

    llm = get_llm()
    assert llm.__class__.__name__ == "DummyCopilotLLM"
    assert llm.api_key == "copilot-key"
