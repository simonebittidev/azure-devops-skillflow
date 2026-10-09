"""LangGraph ReAct agent that executes an LLM Skill against a Pull Request."""

from __future__ import annotations

import logging
from typing import TypedDict, Annotated

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel
from langgraph.errors import GraphRecursionError
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from .models.skill import Skill, PRContext
from .providers.provider_factory import create_chat_model
from .skill_loader import resolve_api_key
from .tools.azdo_tools import AzDOClient, build_tools
from .tools.comment_tracking import CommentTracker

INLINE_COMMENT_TOOLS = {"post_inline_comment", "post_suggestion_comment"}

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent State
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_agent(skill: Skill, client: AzDOClient, comments: CommentTracker) -> "CompiledGraph":
    """Build and compile a LangGraph ReAct agent for the given skill and PR.

    Args:
        skill: The loaded and validated Skill.
        client: Azure DevOps client bound to the Pull Request.
        comments: Tracker of the skill's comment threads on the Pull Request.

    Returns:
        A compiled LangGraph graph ready to be invoked.
    """
    api_key = resolve_api_key(skill)
    llm: BaseChatModel = create_chat_model(skill, api_key)

    tools = build_tools(client, comments, skill.tools, skill.create_pr_target)
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

    client = AzDOClient(ctx)
    comments = CommentTracker(client, skill.name)
    posts_inline = bool(INLINE_COMMENT_TOOLS & set(skill.tools))
    agent = build_agent(skill, client, comments)

    if posts_inline and skill.frontmatter.close_outdated_comments:
        try:
            closed = comments.close_outdated()
            if closed:
                print(f"  Closed {len(closed)} outdated comment thread(s) from previous runs: "
                      + ", ".join(f"#{t}" for t in closed))
        except Exception as exc:
            print(f"##vso[task.logissue type=warning]Skill '{skill.name}': could not close outdated comments: {exc}")

    task = (
        f"Please analyze Pull Request #{ctx.pull_request_id} "
        f"in project '{ctx.project}' and perform the task described above."
    )
    if posts_inline or "post_pr_comment" in skill.tools:
        task += (
            "\n\nThis skill may already have run on earlier versions of this PR. "
            "Inline comments that repeat one of your previous comments on unchanged code are "
            "skipped automatically, and calling post_pr_comment replaces your previous general comment."
        )

    initial_messages: list[BaseMessage] = [
        SystemMessage(content=skill.system_prompt),
        HumanMessage(content=task),
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

    try:
        final_state = agent.invoke({"messages": initial_messages}, config=config)
    except GraphRecursionError:
        print(
            f"##vso[task.logissue type=warning]Skill '{skill.name}' stopped after max_iterations "
            f"({skill.max_iterations}) before finishing; its output may be incomplete. "
            "Increase max_iterations in the skill frontmatter."
        )
        return "Skill stopped: max_iterations reached."

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
