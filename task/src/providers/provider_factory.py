from langchain_core.language_models import BaseChatModel

from ..models.skill import Skill, ProviderType


def create_chat_model(skill: Skill, api_key: str) -> BaseChatModel:
    """Build a LangChain ChatModel for the provider and model specified in the skill.

    Args:
        skill: The loaded Skill, providing provider type, model name, and extras.
        api_key: The resolved API key (or empty string for Ollama).

    Returns:
        A LangChain BaseChatModel ready to be used with LangGraph.

    Raises:
        ValueError: If the provider is not supported.
        ImportError: If the required langchain provider package is not installed.
    """
    provider: ProviderType = skill.provider
    model_name: str = skill.model
    fm = skill.frontmatter

    if provider == "claude":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model_name,
            api_key=api_key,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
        )

    if provider == "azure_openai":
        from langchain_openai import AzureChatOpenAI

        if not fm.azure_endpoint:
            raise ValueError(
                "Skill frontmatter must include 'azure_endpoint' for provider 'azure_openai'."
            )
        if not fm.azure_api_version:
            raise ValueError(
                "Skill frontmatter must include 'azure_api_version' for provider 'azure_openai'."
            )

        return AzureChatOpenAI(
            azure_endpoint=fm.azure_endpoint,
            api_version=fm.azure_api_version,
            azure_deployment=fm.azure_deployment or model_name,
            api_key=api_key,
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model_name,
            base_url=fm.ollama_base_url,
        )

    raise ValueError(
        f"Unsupported provider '{provider}'. "
        "Valid values: claude, openai, azure_openai, ollama."
    )
