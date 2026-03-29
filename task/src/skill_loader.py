import os
from pathlib import Path

import frontmatter

from .models.skill import Skill, SkillFrontmatter


class SkillLoadError(Exception):
    pass


def load_skill(skill_file: str | Path) -> Skill:
    """Load and validate a Skill from a Markdown file with YAML frontmatter.

    The file format is:
        ---
        name: code-review
        provider: claude
        model: claude-sonnet-4-6
        api_key_var: ANTHROPIC_API_KEY
        output: comments
        tools:
          - get_pr_diff
          - post_inline_comment
        ---

        # Agent instructions in plain markdown...

    Args:
        skill_file: Path to the .md skill file.

    Returns:
        A validated Skill instance.

    Raises:
        SkillLoadError: If the file is missing, malformed, or fails validation.
    """
    path = Path(skill_file)

    if not path.exists():
        raise SkillLoadError(f"Skill file not found: {path}")

    if not path.suffix == ".md":
        raise SkillLoadError(f"Skill file must have a .md extension, got: {path.suffix}")

    try:
        post = frontmatter.load(str(path))
    except Exception as exc:
        raise SkillLoadError(f"Failed to parse skill file '{path}': {exc}") from exc

    if not post.metadata:
        raise SkillLoadError(
            f"Skill file '{path}' has no YAML frontmatter. "
            "Add configuration between --- delimiters at the top of the file."
        )

    system_prompt = post.content.strip()
    if not system_prompt:
        raise SkillLoadError(
            f"Skill file '{path}' has no markdown body (system prompt). "
            "Add instructions for the agent after the closing --- delimiter."
        )

    try:
        fm = SkillFrontmatter(**post.metadata)
    except Exception as exc:
        raise SkillLoadError(
            f"Invalid frontmatter in skill file '{path}': {exc}"
        ) from exc

    try:
        fm.validate_tools()
    except ValueError as exc:
        raise SkillLoadError(str(exc)) from exc

    return Skill(frontmatter=fm, system_prompt=system_prompt)


def resolve_api_key(skill: Skill) -> str:
    """Read the LLM API key from the environment variable named in the skill.

    Args:
        skill: The loaded Skill instance.

    Returns:
        The API key string.

    Raises:
        SkillLoadError: If the environment variable is not set or empty.
    """
    var_name = skill.frontmatter.api_key_var
    value = os.environ.get(var_name, "").strip()
    if not value:
        raise SkillLoadError(
            f"Environment variable '{var_name}' (api_key_var for skill '{skill.name}') "
            "is not set. Set it in your Azure DevOps Variable Group."
        )
    return value
