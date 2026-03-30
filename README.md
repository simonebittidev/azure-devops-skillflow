# SkillFlow

An Azure DevOps Pipeline Task that runs **LLM-powered Skills** on Pull Requests. Define agent behavior in plain Markdown files, then let Claude, OpenAI, Azure OpenAI, or Ollama review your code, generate tests, write documentation, and more — automatically, on every PR.

---

## How It Works

Each **Skill** is a `.md` file with two parts:

- **YAML frontmatter** — machine-readable configuration: which LLM provider, model, tools, and output type to use
- **Markdown body** — the system prompt: natural-language instructions for the agent

When the Pipeline Task runs, it:

1. Loads the Skill file and validates the frontmatter
2. Instantiates the correct LLM via LangChain (Claude, OpenAI, Azure OpenAI, or Ollama)
3. Builds a **LangGraph ReAct agent** equipped with Azure DevOps tools
4. Lets the agent autonomously call tools (fetch diff, read files, post comments, create commits, open PRs) until the task is complete

The agent is autonomous — it decides which tools to call, in what order, and when to stop. You control its behavior entirely through the Markdown instructions.

---

## Architecture

```
Azure DevOps Pipeline
  └── RunLLMSkill Task (Python)
       │
       ├── skill_loader.py     — parses .md frontmatter + body
       ├── provider_factory.py — creates LangChain ChatModel
       ├── azdo_tools.py       — LangGraph tools (AzDO REST API)
       └── agent.py            — LangGraph ReAct graph

              LangGraph Agent
              ├── [tool] get_pr_diff
              ├── [tool] list_changed_files
              ├── [tool] get_file_content
              ├── [tool] post_inline_comment
              ├── [tool] post_pr_comment
              ├── [tool] create_commit
              └── [tool] create_pr
```

**Tech stack:**
- [LangGraph](https://langchain-ai.github.io/langgraph/) — agent orchestration (ReAct loop)
- [LangChain](https://python.langchain.com/) — unified LLM interface across providers
- [python-frontmatter](https://python-frontmatter.readthedocs.io/) — Markdown + YAML frontmatter parsing
- [Pydantic v2](https://docs.pydantic.dev/) — skill schema validation
- Azure DevOps REST API (via `requests`) — PR interaction

---

## Skill File Format

Skills live in `.md` files anywhere in your repository. Convention: `.azdo/skills/<skill-name>.md`.

```markdown
---
name: code-review
description: "Reviews PR changes and posts inline comments"
version: "1.0"

# LLM configuration
provider: claude                   # claude | openai | azure_openai | ollama
model: claude-sonnet-4-6
api_key_var: ANTHROPIC_API_KEY     # name of the AzDO Variable Group variable

# What the agent outputs
output: comments                   # comments | commit | new_pr

# Agent limits
max_iterations: 15

# Which AzDO tools the agent can use
tools:
  - get_pr_diff
  - list_changed_files
  - get_file_content
  - post_inline_comment
  - post_pr_comment
---

# Code Review Agent

You are an expert software engineer performing a thorough code review...

## How to Proceed
1. Call `get_pr_diff` to understand what changed
2. ...
```

### Frontmatter Reference

| Field | Required | Description |
|---|---|---|
| `name` | yes | Identifier for the skill |
| `provider` | yes | `claude`, `openai`, `azure_openai`, or `ollama` |
| `model` | yes | Model name (e.g. `claude-sonnet-4-6`, `gpt-4o`) |
| `api_key_var` | yes | Name of the env var holding the API key |
| `output` | no | `comments` (default), `commit`, or `new_pr` |
| `max_iterations` | no | Max agent steps, default `10`, max `50` |
| `tools` | no | List of tools to enable (see below) |
| `azure_endpoint` | azure_openai only | Azure OpenAI endpoint URL |
| `azure_api_version` | azure_openai only | API version string |
| `azure_deployment` | azure_openai only | Deployment name |
| `ollama_base_url` | ollama only | Default: `http://localhost:11434` |

### Available Tools

| Tool | Description |
|---|---|
| `get_pr_diff` | Fetches the diff summary for the PR (changed files + change types) |
| `list_changed_files` | Returns the list of file paths modified in the PR |
| `get_file_content` | Returns the full content of a file from the PR's source branch |
| `post_pr_comment` | Posts a general comment thread on the PR |
| `post_inline_comment` | Posts an inline comment on a specific file and line |
| `create_commit` | Creates a commit with file changes on the PR's source branch |
| `create_pr` | Creates a new branch + PR with the given file changes |

### Output Types

| Output | What happens |
|---|---|
| `comments` | Agent posts inline/general comments on the PR. Nothing is committed. |
| `commit` | Agent commits file changes directly to the PR's source branch. |
| `new_pr` | Agent creates a new branch and opens a separate PR with the changes. |

---

## Getting Started

### 1. Install the Extension

Publish the extension to your Azure DevOps organization (see [Packaging & Publishing](#packaging--publishing)).

### 2. Create Your Skill Files

Drop Skill `.md` files in `.azdo/skills/` in your repository. SkillFlow picks them all up automatically — no explicit configuration needed.

```bash
mkdir -p .azdo/skills
cp examples/code-review.md .azdo/skills/code-review.md
```

Multiple skills are all executed in sequence on every PR:

```
.azdo/
  skills/
    code-review.md      ← runs 1st
    test-generator.md   ← runs 2nd
    doc-writer.md       ← runs 3rd
```

### 3. Set Up Secrets

In your Azure DevOps project, create a **Variable Group** (Pipelines → Library → + Variable Group) with your LLM API keys:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your Anthropic API key |
| `OPENAI_API_KEY` | your OpenAI API key (if using OpenAI) |

Mark them as secret (🔒). Link the Variable Group to your pipeline.

### 4. Add the Task to Your Pipeline

```yaml
# azure-pipelines.yml
trigger: none
pr:
  - main

variables:
  - group: llm-api-keys   # your Variable Group name

steps:
  - task: RunLLMSkill@0
    env:
      ANTHROPIC_API_KEY: $(ANTHROPIC_API_KEY)
```

That's it. SkillFlow reads all pipeline context (organization, project, repo, PR ID, token) directly from the Azure DevOps environment — no manual configuration required.

#### Optional inputs

```yaml
  - task: RunLLMSkill@0
    inputs:
      skillsDir: '.azdo/skills'   # default — change if you use a different path
      verbose: false              # set to true to log agent reasoning steps
    env:
      ANTHROPIC_API_KEY: $(ANTHROPIC_API_KEY)
```

> **Note:** Enable **"Allow scripts to access the OAuth token"** in your pipeline settings (Pipeline → Edit → Triggers → ... → Allow scripts to access the OAuth token), and grant the Build Service **"Contribute to pull requests"** permission on the repository (Project Settings → Repositories → Security).

### 5. Open a Pull Request

That's it. The next time a PR is opened or updated against `main`, the pipeline runs and the agent posts its output.

---

## Example Skills

### `code-review.md` — Claude

Posts inline review comments with `[HIGH/MEDIUM/LOW]` severity ratings, plus a final summary comment. Uses `post_inline_comment` and `post_pr_comment`.

### `test-generator.md` — Claude

Reads modified source files, generates unit tests, and opens a new PR with the test files. Uses `create_pr`.

### `doc-writer.md` — OpenAI GPT-4o

Adds or improves docstrings for all public functions and classes changed in the PR, then commits the updated files directly to the branch. Uses `create_commit`.

---

## Packaging & Publishing

The extension uses the [Azure DevOps Extension SDK](https://github.com/microsoft/azure-devops-extension-sdk). To package and publish:

```bash
# Install tfx-cli
npm install -g tfx-cli

# Install Python dependencies (for local testing)
pip install -r task/requirements.txt

# Package the extension
cd extension
tfx extension create --manifest-globs vss-extension.json

# Publish to the Marketplace (requires a publisher account)
tfx extension publish --manifest-globs vss-extension.json --token <your-PAT>
```

The packaged `.vsix` file can also be installed directly into an Azure DevOps organization without going through the public Marketplace.

---

## Project Structure

```
skillflow/
├── task/                          # Pipeline Task
│   ├── task.json                  # AzDO task manifest (inputs, version, execution)
│   ├── main.py                    # Entry point: reads AzDO inputs → runs agent
│   ├── requirements.txt           # Python dependencies
│   └── src/
│       ├── models/skill.py        # Pydantic models: SkillFrontmatter, Skill, PRContext
│       ├── skill_loader.py        # .md parser (python-frontmatter) + validation
│       ├── providers/
│       │   └── provider_factory.py # LangChain ChatModel factory (multi-provider)
│       ├── tools/
│       │   └── azdo_tools.py      # LangGraph tools wrapping AzDO REST API
│       └── agent.py               # LangGraph ReAct graph + run_skill()
├── extension/
│   └── vss-extension.json         # Extension manifest for AzDO Marketplace
└── examples/
    ├── code-review.md             # Example: inline code review (Claude)
    ├── test-generator.md          # Example: unit test generation (Claude)
    └── doc-writer.md              # Example: docstring writer (OpenAI)
```

---

## LangSmith Tracing

[LangSmith](https://smith.langchain.com) provides observability for LangChain and LangGraph applications. When enabled, every skill execution — each LLM call, tool invocation, and agent step — is recorded as a structured trace you can inspect, debug, and compare in the LangSmith UI.

### What tracing enables

- Full execution traces for every PR skill run, with inputs and outputs at each node
- Latency and token usage breakdowns per LLM call
- Filtering and search by skill name, provider, PR ID, and repository
- Side-by-side comparison of runs across different models or prompt versions

### Task inputs

| Input | Default | Description |
|---|---|---|
| `langsmith_project` | _(empty — disabled)_ | LangSmith project name. Leave empty to disable tracing. |
| `langsmith_api_key_var` | `LANGCHAIN_API_KEY` | Name of the environment variable holding the LangSmith API key. |

### How to configure in Azure DevOps

**Step 1: Store the API key as a secret variable**

In your Azure DevOps project, go to **Pipelines → Library** and create or open a Variable Group (e.g. `langsmith-credentials`). Add a variable:

- Name: `LANGCHAIN_API_KEY`
- Value: your LangSmith API key (get it from [smith.langchain.com](https://smith.langchain.com) → Settings → API Keys)
- Lock the variable (🔒 secret)

**Step 2: Link the Variable Group to your pipeline**

```yaml
variables:
  - group: llm-api-keys           # existing group with LLM provider keys
  - group: langsmith-credentials  # new group with LANGCHAIN_API_KEY
```

**Step 3: Configure the task**

Pass the `langsmith_project` input and map the secret variable into the task's environment:

```yaml
- task: RunLLMSkill@0
  inputs:
    skillsDir: '.azdo/skills'
    langsmith_project: 'my-project-pr-reviews'
    langsmith_api_key_var: 'LANGCHAIN_API_KEY'
  env:
    ANTHROPIC_API_KEY: $(ANTHROPIC_API_KEY)
    LANGCHAIN_API_KEY: $(LANGCHAIN_API_KEY)   # required: secrets must be explicitly mapped
```

> **Note:** The `env:` block is required because Azure DevOps does not automatically expose secret variables as environment variables — they must be explicitly mapped.

### How to view traces

1. Go to [smith.langchain.com](https://smith.langchain.com) and sign in.
2. Select the project matching your `langsmith_project` input.
3. Each skill run appears as a top-level trace named after the skill (e.g. `code-review`, `test-generator`).
4. Use the **Filter** panel to filter by tags (`code-review`, `claude`) or metadata fields (`pr_id`, `repository_id`, `project`).

### Disabling tracing

Leave `langsmith_project` empty (the default). When tracing is disabled, a debug log message is printed and execution continues normally with no performance impact.
