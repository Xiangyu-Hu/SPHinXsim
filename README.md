# ![SPHinXsys Logo](assets/images/logo.png) SPHinXsim

The python and LLM UI for the multi-physics library SPHinXsys.

## LLM Providers

SPHinXsim can generate config.json from natural-language prompts with multiple providers.

### Mock provider (default)

No network dependency:

```bash
sphinxsim generate "water dam break for 1 second" -o config.json
```

### Copilot-compatible provider

Set the provider and credentials, then generate config.json:

```bash
export SPHINXSIM_LLM_PROVIDER=copilot
export COPILOT_MODEL=gpt-4o-mini
export COPILOT_API_KEY=<your-token>
export COPILOT_BASE_URL=<your-copilot-or-github-models-endpoint>

sphinxsim generate "2D dam break in a 2 m domain for 1.5 s" -o config.json
```

You can also use these fallbacks:
- API key fallback: GITHUB_TOKEN
- Base URL fallback: GITHUB_MODELS_BASE_URL

Install optional LLM dependencies when needed:

```bash
pip install -e .[llm]
```
