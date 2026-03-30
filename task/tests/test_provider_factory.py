"""
Unit tests for src/providers/provider_factory.py

All LangChain chat-model classes are patched so no real network calls or API
keys are required.  The conftest.py ensures that provider packages are stubbed
in sys.modules even when not installed.
"""
import pytest
from unittest.mock import MagicMock, patch

from src.providers.provider_factory import create_chat_model
from src.models.skill import Skill, SkillFrontmatter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_API_KEY = "sk-fake-key-for-tests"


def _make_skill(provider: str = "claude", model: str = "claude-sonnet-4-6", **overrides) -> Skill:
    """Build a minimal Skill object for testing."""
    fm = SkillFrontmatter(
        name="test-skill",
        provider=provider,
        model=model,
        api_key_var="TEST_API_KEY",
        **overrides,
    )
    return Skill(frontmatter=fm, system_prompt="You are a test agent.")


# ---------------------------------------------------------------------------
# Claude (Anthropic)
# ---------------------------------------------------------------------------

class TestClaudeProvider:
    def test_instantiates_chat_anthropic(self):
        skill = _make_skill(provider="claude", model="claude-sonnet-4-6")
        mock_cls = MagicMock()
        with patch("langchain_anthropic.ChatAnthropic", mock_cls):
            create_chat_model(skill, FAKE_API_KEY)
        mock_cls.assert_called_once()

    def test_passes_model_name(self):
        skill = _make_skill(provider="claude", model="claude-opus-4-1")
        mock_cls = MagicMock()
        with patch("langchain_anthropic.ChatAnthropic", mock_cls):
            create_chat_model(skill, FAKE_API_KEY)
        assert mock_cls.call_args.kwargs["model"] == "claude-opus-4-1"

    def test_passes_api_key(self):
        skill = _make_skill(provider="claude")
        mock_cls = MagicMock()
        with patch("langchain_anthropic.ChatAnthropic", mock_cls):
            create_chat_model(skill, FAKE_API_KEY)
        assert mock_cls.call_args.kwargs["api_key"] == FAKE_API_KEY

    def test_returns_model_instance(self):
        skill = _make_skill(provider="claude")
        mock_cls = MagicMock()
        with patch("langchain_anthropic.ChatAnthropic", mock_cls):
            result = create_chat_model(skill, FAKE_API_KEY)
        assert result is mock_cls.return_value


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

class TestOpenAIProvider:
    def test_instantiates_chat_openai(self):
        skill = _make_skill(provider="openai", model="gpt-4o")
        mock_cls = MagicMock()
        with patch("langchain_openai.ChatOpenAI", mock_cls):
            create_chat_model(skill, FAKE_API_KEY)
        mock_cls.assert_called_once()

    def test_passes_model_name(self):
        skill = _make_skill(provider="openai", model="gpt-4-turbo")
        mock_cls = MagicMock()
        with patch("langchain_openai.ChatOpenAI", mock_cls):
            create_chat_model(skill, FAKE_API_KEY)
        assert mock_cls.call_args.kwargs["model"] == "gpt-4-turbo"

    def test_passes_api_key(self):
        skill = _make_skill(provider="openai", model="gpt-4o")
        mock_cls = MagicMock()
        with patch("langchain_openai.ChatOpenAI", mock_cls):
            create_chat_model(skill, FAKE_API_KEY)
        assert mock_cls.call_args.kwargs["api_key"] == FAKE_API_KEY

    def test_returns_model_instance(self):
        skill = _make_skill(provider="openai", model="gpt-4o")
        mock_cls = MagicMock()
        with patch("langchain_openai.ChatOpenAI", mock_cls):
            result = create_chat_model(skill, FAKE_API_KEY)
        assert result is mock_cls.return_value


# ---------------------------------------------------------------------------
# Azure OpenAI
# ---------------------------------------------------------------------------

class TestAzureOpenAIProvider:
    _AZURE_DEFAULTS = dict(
        provider="azure_openai",
        model="gpt-4o",
        azure_endpoint="https://my-resource.openai.azure.com/",
        azure_api_version="2024-02-01",
        azure_deployment="my-deployment",
    )

    def test_instantiates_azure_chat_openai(self):
        skill = _make_skill(**self._AZURE_DEFAULTS)
        mock_cls = MagicMock()
        with patch("langchain_openai.AzureChatOpenAI", mock_cls):
            create_chat_model(skill, FAKE_API_KEY)
        mock_cls.assert_called_once()

    def test_passes_endpoint(self):
        skill = _make_skill(**self._AZURE_DEFAULTS)
        mock_cls = MagicMock()
        with patch("langchain_openai.AzureChatOpenAI", mock_cls):
            create_chat_model(skill, FAKE_API_KEY)
        assert mock_cls.call_args.kwargs["azure_endpoint"] == "https://my-resource.openai.azure.com/"

    def test_passes_api_version(self):
        skill = _make_skill(**self._AZURE_DEFAULTS)
        mock_cls = MagicMock()
        with patch("langchain_openai.AzureChatOpenAI", mock_cls):
            create_chat_model(skill, FAKE_API_KEY)
        assert mock_cls.call_args.kwargs["api_version"] == "2024-02-01"

    def test_passes_deployment(self):
        skill = _make_skill(**self._AZURE_DEFAULTS)
        mock_cls = MagicMock()
        with patch("langchain_openai.AzureChatOpenAI", mock_cls):
            create_chat_model(skill, FAKE_API_KEY)
        assert mock_cls.call_args.kwargs["azure_deployment"] == "my-deployment"

    def test_passes_api_key(self):
        skill = _make_skill(**self._AZURE_DEFAULTS)
        mock_cls = MagicMock()
        with patch("langchain_openai.AzureChatOpenAI", mock_cls):
            create_chat_model(skill, FAKE_API_KEY)
        assert mock_cls.call_args.kwargs["api_key"] == FAKE_API_KEY

    def test_deployment_defaults_to_model_name_when_not_set(self):
        """When azure_deployment is omitted, the model name is used as deployment."""
        overrides = {**self._AZURE_DEFAULTS, "azure_deployment": None}
        skill = _make_skill(**overrides)
        mock_cls = MagicMock()
        with patch("langchain_openai.AzureChatOpenAI", mock_cls):
            create_chat_model(skill, FAKE_API_KEY)
        assert mock_cls.call_args.kwargs["azure_deployment"] == "gpt-4o"

    def test_raises_when_azure_endpoint_missing(self):
        overrides = {**self._AZURE_DEFAULTS, "azure_endpoint": None}
        skill = _make_skill(**overrides)
        mock_cls = MagicMock()
        with patch("langchain_openai.AzureChatOpenAI", mock_cls):
            with pytest.raises(ValueError, match="azure_endpoint"):
                create_chat_model(skill, FAKE_API_KEY)

    def test_raises_when_azure_api_version_missing(self):
        overrides = {**self._AZURE_DEFAULTS, "azure_api_version": None}
        skill = _make_skill(**overrides)
        mock_cls = MagicMock()
        with patch("langchain_openai.AzureChatOpenAI", mock_cls):
            with pytest.raises(ValueError, match="azure_api_version"):
                create_chat_model(skill, FAKE_API_KEY)

    def test_returns_model_instance(self):
        skill = _make_skill(**self._AZURE_DEFAULTS)
        mock_cls = MagicMock()
        with patch("langchain_openai.AzureChatOpenAI", mock_cls):
            result = create_chat_model(skill, FAKE_API_KEY)
        assert result is mock_cls.return_value


# ---------------------------------------------------------------------------
# Ollama (local)
# ---------------------------------------------------------------------------

class TestOllamaProvider:
    def test_instantiates_chat_ollama(self):
        skill = _make_skill(provider="ollama", model="llama3")
        mock_cls = MagicMock()
        with patch("langchain_ollama.ChatOllama", mock_cls):
            create_chat_model(skill, "")
        mock_cls.assert_called_once()

    def test_passes_model_name(self):
        skill = _make_skill(provider="ollama", model="mistral")
        mock_cls = MagicMock()
        with patch("langchain_ollama.ChatOllama", mock_cls):
            create_chat_model(skill, "")
        assert mock_cls.call_args.kwargs["model"] == "mistral"

    def test_uses_default_base_url(self):
        skill = _make_skill(provider="ollama", model="llama3")
        mock_cls = MagicMock()
        with patch("langchain_ollama.ChatOllama", mock_cls):
            create_chat_model(skill, "")
        assert mock_cls.call_args.kwargs["base_url"] == "http://localhost:11434"

    def test_uses_custom_base_url(self):
        skill = _make_skill(
            provider="ollama",
            model="llama3",
            ollama_base_url="http://my-ollama-server:11434",
        )
        mock_cls = MagicMock()
        with patch("langchain_ollama.ChatOllama", mock_cls):
            create_chat_model(skill, "")
        assert mock_cls.call_args.kwargs["base_url"] == "http://my-ollama-server:11434"

    def test_accepts_empty_api_key(self):
        """Ollama is a local model — no API key is needed."""
        skill = _make_skill(provider="ollama", model="llama3")
        mock_cls = MagicMock()
        with patch("langchain_ollama.ChatOllama", mock_cls):
            create_chat_model(skill, "")
        mock_cls.assert_called_once()

    def test_returns_model_instance(self):
        skill = _make_skill(provider="ollama", model="llama3")
        mock_cls = MagicMock()
        with patch("langchain_ollama.ChatOllama", mock_cls):
            result = create_chat_model(skill, "")
        assert result is mock_cls.return_value


# ---------------------------------------------------------------------------
# Unsupported provider
# ---------------------------------------------------------------------------

class TestUnsupportedProvider:
    def test_raises_value_error(self):
        """Inject an invalid provider value by bypassing Pydantic validation."""
        fm = SkillFrontmatter.model_construct(
            name="test-skill",
            provider="unknown_provider",
            model="some-model",
            api_key_var="TEST_API_KEY",
            description="",
            version="1.0",
            output="comments",
            max_iterations=10,
            tools=["get_pr_diff"],
            azure_endpoint=None,
            azure_api_version=None,
            azure_deployment=None,
            ollama_base_url="http://localhost:11434",
        )
        skill = Skill.model_construct(frontmatter=fm, system_prompt="test")
        with pytest.raises(ValueError, match="Unsupported provider"):
            create_chat_model(skill, FAKE_API_KEY)

    def test_error_message_lists_valid_providers(self):
        fm = SkillFrontmatter.model_construct(
            name="test-skill",
            provider="unknown_provider",
            model="some-model",
            api_key_var="TEST_API_KEY",
            description="",
            version="1.0",
            output="comments",
            max_iterations=10,
            tools=["get_pr_diff"],
            azure_endpoint=None,
            azure_api_version=None,
            azure_deployment=None,
            ollama_base_url="http://localhost:11434",
        )
        skill = Skill.model_construct(frontmatter=fm, system_prompt="test")
        with pytest.raises(ValueError) as exc_info:
            create_chat_model(skill, FAKE_API_KEY)
        msg = str(exc_info.value)
        assert "claude" in msg
        assert "openai" in msg
        assert "ollama" in msg
