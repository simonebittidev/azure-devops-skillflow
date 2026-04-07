"""LangGraph ReAct agent that executes an LLM Skill against a Pull Request."""

from __future__ import annotations

import logging
from typing import TypedDict, Annotated

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from .models.skill import Skill, PRContext
from .providers.provider_factory import create_chat_model
from .skill_loader import resolve_api_key
from .tools.azdo_tools import build_tools

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent State
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_agent(skill: Skill, ctx: PRContext) -> "CompiledGraph":
    """Build and compile a LangGraph ReAct agent for the given skill and PR context.

    Args:
        skill: The loaded and validated Skill.
        ctx: The Pull Request context (credentials + identifiers).

    Returns:
        A compiled LangGraph graph ready to be invoked.
    """
    api_key = resolve_api_key(skill)
    llm: BaseChatModel = create_chat_model(skill, api_key)

    tools = build_tools(ctx, skill.tools, skill.create_pr_target)
    llm_with_tools = llm.bind_tools(tools)

    def call_model(state: AgentState) -> AgentState:
        logger.debug("Agent calling LLM with %d messages", len(state["messages"]))
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    tool_node = ToolNode(tools)

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", tool_node)

    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")

    return graph.compile()


# ---------------------------------------------------------------------------
# High-level runner
# ---------------------------------------------------------------------------

def run_skill(skill: Skill, ctx: PRContext, verbose: bool = False) -> str:
    """Execute a Skill against a Pull Request using a LangGraph agent.

    Args:
        skill: The loaded and validated Skill.
        ctx: The Pull Request context.
        verbose: If True, log each message in the agent's reasoning trace.

    Returns:
        The final text response from the agent.
    """
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    logger.info(
        "Running skill '%s' on PR #%d (provider: %s / model: %s)",
        skill.name,
        ctx.pull_request_id,
        skill.provider,
        skill.model,
    )

    agent = build_agent(skill, ctx)

    initial_messages: list[BaseMessage] = [
        SystemMessage(content=skill.system_prompt),
        HumanMessage(
            content=(
                f"Please analyze Pull Request #{ctx.pull_request_id} "
                f"in project '{ctx.project}' and perform the task described above."
            )
        ),
    ]

    config = {
        "recursion_limit": skill.max_iterations * 2 + 1,
        "run_name": skill.name,
        "tags": [skill.name, skill.provider],
        "metadata": {
            "skill": skill.name,
            "provider": skill.provider,
            "pr_id": str(ctx.pull_request_id),
            "repository_id": ctx.repository_id,
            "project": ctx.project,
        },
    }

    final_state = agent.invoke({"messages": initial_messages}, config=config)

    messages = final_state.get("messages", [])

    if verbose:
        for msg in messages:
            role = type(msg).__name__
            content = str(msg.content)[:200]
            logger.debug("[%s] %s", role, content)

    # Return the last AI message content
    for msg in reversed(messages):
        if hasattr(msg, "content") and msg.content:
            return str(msg.content)

    return "Skill completed with no final message."
