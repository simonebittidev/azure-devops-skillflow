"""
Unit tests for src/skill_loader.py

Tests cover load_skill() and resolve_api_key() including valid inputs,
all error paths, and environment variable handling.  No real API calls
are made.
"""
import os
import textwrap
import pytest
from pathlib import Path
from unittest.mock import patch

from src.skill_loader import load_skill, resolve_api_key, SkillLoadError
from src.models.skill import Skill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, filename: str, content: str) -> Path:
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content))
    return p


VALID_SKILL = """\
    ---
    name: code-review
    provider: claude
    model: claude-sonnet-4-6
    api_key_var: ANTHROPIC_API_KEY
    ---

    You are a code review assistant.
    """


# ---------------------------------------------------------------------------
# load_skill — happy path
# ---------------------------------------------------------------------------

class TestLoadSkillValid:
    def test_returns_skill_instance(self, tmp_path):
        f = _write(tmp_path, "skill.md", VALID_SKILL)
        skill = load_skill(f)
        assert isinstance(skill, Skill)

    def test_parses_name(self, tmp_path):
        f = _write(tmp_path, "skill.md", VALID_SKILL)
        assert load_skill(f).name == "code-review"

    def test_parses_provider(self, tmp_path):
        f = _write(tmp_path, "skill.md", VALID_SKILL)
        assert load_skill(f).provider == "claude"

    def test_parses_model(self, tmp_path):
        f = _write(tmp_path, "skill.md", VALID_SKILL)
        assert load_skill(f).model == "claude-sonnet-4-6"

    def test_parses_system_prompt(self, tmp_path):
        f = _write(tmp_path, "skill.md", VALID_SKILL)
        assert "code review assistant" in load_skill(f).system_prompt

    def test_default_tools_are_set(self, tmp_path):
        f = _write(tmp_path, "skill.md", VALID_SKILL)
        skill = load_skill(f)
        assert "get_pr_diff" in skill.tools
        assert "list_changed_files" in skill.tools
        assert "post_pr_comment" in skill.tools

    def test_custom_tools_are_parsed(self, tmp_path):
        content = """\
            ---
            name: slim-skill
            provider: openai
            model: gpt-4o
            api_key_var: OPENAI_API_KEY
            tools:
              - get_pr_diff
              - post_pr_comment
            ---

            Custom prompt.
            """
        f = _write(tmp_path, "skill.md", content)
        skill = load_skill(f)
        assert skill.tools == ["get_pr_diff", "post_pr_comment"]

    def test_accepts_path_string(self, tmp_path):
        f = _write(tmp_path, "skill.md", VALID_SKILL)
        skill = load_skill(str(f))
        assert skill.name == "code-review"

    def test_parses_azure_openai_provider(self, tmp_path):
        content = """\
            ---
            name: azure-skill
            provider: azure_openai
            model: gpt-4o
            api_key_var: AZURE_API_KEY
            azure_endpoint: https://my-resource.openai.azure.com/
            azure_api_version: "2024-02-01"
            azure_deployment: my-deployment
            ---

            You are an Azure-powered assistant.
            """
        f = _write(tmp_path, "skill.md", content)
        skill = load_skill(f)
        assert skill.provider == "azure_openai"
        assert skill.frontmatter.azure_endpoint == "https://my-resource.openai.azure.com/"
        assert skill.frontmatter.azure_api_version == "2024-02-01"
        assert skill.frontmatter.azure_deployment == "my-deployment"

    def test_parses_ollama_provider_with_custom_base_url(self, tmp_path):
        content = """\
            ---
            name: ollama-skill
            provider: ollama
            model: llama3
            api_key_var: DUMMY_KEY
            ollama_base_url: http://my-ollama:11434
            ---

            You are a local assistant.
            """
        f = _write(tmp_path, "skill.md", content)
        skill = load_skill(f)
        assert skill.provider == "ollama"
        assert skill.frontmatter.ollama_base_url == "http://my-ollama:11434"


# ---------------------------------------------------------------------------
# load_skill — error paths
# ---------------------------------------------------------------------------

class TestLoadSkillErrors:
    def test_raises_if_file_not_found(self, tmp_path):
        with pytest.raises(SkillLoadError, match="not found"):
            load_skill(tmp_path / "missing.md")

    def test_raises_if_extension_is_not_md(self, tmp_path):
        f = tmp_path / "skill.txt"
        f.write_text(VALID_SKILL)
        with pytest.raises(SkillLoadError, match=r"\.md"):
            load_skill(f)

    def test_raises_if_no_frontmatter(self, tmp_path):
        f = _write(tmp_path, "skill.md", "No frontmatter here, just plain text.")
        with pytest.raises(SkillLoadError, match="frontmatter"):
            load_skill(f)

    def test_raises_if_system_prompt_is_empty(self, tmp_path):
        content = """\
            ---
            name: empty-prompt
            provider: claude
            model: claude-sonnet-4-6
            api_key_var: ANTHROPIC_API_KEY
            ---
            """
        f = _write(tmp_path, "skill.md", content)
        with pytest.raises(SkillLoadError, match="system prompt"):
            load_skill(f)

    def test_raises_if_required_field_missing(self, tmp_path):
        # 'model' is required but missing
        content = """\
            ---
            name: no-model
            provider: claude
            api_key_var: ANTHROPIC_API_KEY
            ---

            Some prompt.
            """
        f = _write(tmp_path, "skill.md", content)
        with pytest.raises(SkillLoadError, match="Invalid frontmatter"):
            load_skill(f)

    def test_raises_if_unknown_tool_listed(self, tmp_path):
        content = """\
            ---
            name: bad-tools
            provider: claude
            model: claude-sonnet-4-6
            api_key_var: ANTHROPIC_API_KEY
            tools:
              - nonexistent_tool
            ---

            Some prompt.
            """
        f = _write(tmp_path, "skill.md", content)
        with pytest.raises(SkillLoadError, match="Unknown tools"):
            load_skill(f)

    def test_raises_if_provider_is_invalid(self, tmp_path):
        content = """\
            ---
            name: bad-provider
            provider: unsupported_llm
            model: some-model
            api_key_var: SOME_KEY
            ---

            Some prompt.
            """
        f = _write(tmp_path, "skill.md", content)
        with pytest.raises(SkillLoadError, match="Invalid frontmatter"):
            load_skill(f)

    def test_raises_if_max_iterations_out_of_range(self, tmp_path):
        content = """\
            ---
            name: bad-iterations
            provider: claude
            model: claude-sonnet-4-6
            api_key_var: ANTHROPIC_API_KEY
            max_iterations: 999
            ---

            Some prompt.
            """
        f = _write(tmp_path, "skill.md", content)
        with pytest.raises(SkillLoadError, match="Invalid frontmatter"):
            load_skill(f)


# ---------------------------------------------------------------------------
# resolve_api_key
# ---------------------------------------------------------------------------

class TestResolveApiKey:
    def _load(self, tmp_path: Path) -> Skill:
        f = _write(tmp_path, "skill.md", VALID_SKILL)
        return load_skill(f)

    def test_returns_key_from_env(self, tmp_path):
        skill = self._load(tmp_path)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-key"}):
            assert resolve_api_key(skill) == "sk-test-key"

    def test_raises_if_env_var_not_set(self, tmp_path):
        skill = self._load(tmp_path)
        env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True):
            with pytest.raises(SkillLoadError, match="ANTHROPIC_API_KEY"):
                resolve_api_key(skill)

    def test_raises_if_env_var_is_empty_string(self, tmp_path):
        skill = self._load(tmp_path)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with pytest.raises(SkillLoadError, match="ANTHROPIC_API_KEY"):
                resolve_api_key(skill)

    def test_raises_if_env_var_is_whitespace_only(self, tmp_path):
        skill = self._load(tmp_path)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "   "}):
            with pytest.raises(SkillLoadError):
                resolve_api_key(skill)

    def test_trims_whitespace_from_valid_key(self, tmp_path):
        skill = self._load(tmp_path)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "  sk-real-key  "}):
            assert resolve_api_key(skill) == "sk-real-key"
