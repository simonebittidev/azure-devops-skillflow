"""
pytest configuration and shared fixtures.

LangChain provider packages (langchain_anthropic, langchain_openai,
langchain_ollama) are mocked at the sys.modules level so that the test suite
runs without requiring real API credentials, network access, or the packages
to be installed.
"""
import sys
from unittest.mock import MagicMock

_PROVIDER_MODULES = [
    # LangChain core (imported at module level in provider_factory)
    "langchain_core",
    "langchain_core.language_models",
    # Provider-specific packages
    "langchain_anthropic",
    "langchain_openai",
    "langchain_ollama",
]

for _mod in _PROVIDER_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
