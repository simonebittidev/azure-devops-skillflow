from typing import Literal
from pydantic import BaseModel, Field


ProviderType = Literal["claude", "openai", "azure_openai", "ollama"]
OutputType = Literal["comments", "commit", "new_pr"]

AVAILABLE_TOOLS = {
    "get_pr_diff",
    "get_file_content",
    "list_changed_files",
    "post_inline_comment",
    "post_pr_comment",
    "create_commit",
    "create_pr",
}


class SkillFrontmatter(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0"
    provider: ProviderType
    model: str
    api_key_var: str = Field(
        description="Name of the environment variable holding the LLM API key"
    )
    output: OutputType = "comments"
    max_iterations: int = Field(default=10, ge=1, le=50)
    tools: list[str] = Field(
        default_factory=lambda: [
            "get_pr_diff",
            "list_changed_files",
            "get_file_content",
            "post_inline_comment",
            "post_pr_comment",
        ]
    )
    # Azure OpenAI specific
    azure_endpoint: str | None = None
    azure_api_version: str | None = None
    azure_deployment: str | None = None
    # Ollama specific
    ollama_base_url: str = "http://localhost:11434"

    def validate_tools(self) -> list[str]:
        unknown = set(self.tools) - AVAILABLE_TOOLS
        if unknown:
            raise ValueError(f"Unknown tools in skill definition: {unknown}")
        return self.tools


class Skill(BaseModel):
    frontmatter: SkillFrontmatter
    system_prompt: str

    @property
    def name(self) -> str:
        return self.frontmatter.name

    @property
    def provider(self) -> ProviderType:
        return self.frontmatter.provider

    @property
    def model(self) -> str:
        return self.frontmatter.model

    @property
    def output(self) -> OutputType:
        return self.frontmatter.output

    @property
    def tools(self) -> list[str]:
        return self.frontmatter.tools

    @property
    def max_iterations(self) -> int:
        return self.frontmatter.max_iterations


class PRContext(BaseModel):
    organization_url: str
    project: str
    repository_id: str
    pull_request_id: int
    access_token: str
