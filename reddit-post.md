# Reddit Promotion Post — SkillFlow

## Suggested subreddits

- **r/devops** — Primary target (DevOps practitioners)
- **r/azuredevops** — Niche but highly relevant
- **r/programming** — Broader developer audience
- **r/MachineLearning** or **r/LocalLLaMA** — For the LLM/AI angle

---

## Post title options

1. `I built an Azure DevOps task that runs LLM agents on your PRs — define behaviors in plain Markdown`
2. `SkillFlow: AI-powered code review, security scans, and test generation on every PR — no code required, just Markdown`
3. `Tired of skipped code reviews? I made a free Azure DevOps extension that runs autonomous AI agents on every PR`

---

## Post body

---

### Hey r/devops,

I built **SkillFlow**, an open-source Azure DevOps Pipeline Task that runs autonomous LLM agents on Pull Requests — and I wanted to share it here in case it's useful for anyone else.

**The problem I was trying to solve:** Code review is inconsistent. Security checks get skipped. Docs never get written. Every team knows these things *should* happen, but they get squeezed out under deadline pressure. I wanted to automate the boring-but-important parts without writing a custom integration for every use case.

---

### How it works

You drop Markdown files into a `.azdo/skills/` folder in your repo. Each file defines a "Skill" — a system prompt that tells the agent what to do on every PR. SkillFlow picks them up automatically and runs them as part of your pipeline.

A skill looks like this:

```markdown
---
name: Security Scanner
provider: anthropic
model: claude-opus-4-5
output_type: comments
---

You are a security expert reviewing this pull request.
Check for OWASP Top 10 vulnerabilities, hardcoded secrets,
and injection risks. Post inline comments with severity ratings.
```

That's it. No Python, no YAML configs, no custom scripts. The agent autonomously decides which tools to call (get the diff, read files, post inline comments, etc.) based on your prompt.

---

### What agents can do on a PR

- Read the full diff or individual file diffs
- Post **inline comments** at specific line numbers
- Post **suggestion comments** (renders as one-click "Apply suggestion" in Azure DevOps UI)
- **Commit changes directly** to the PR branch (useful for auto-fixing docs or updating changelogs)
- **Open a new PR** with generated content (e.g. auto-generated unit tests in a separate branch)

---

### LLM providers supported

Works with Claude (Anthropic), OpenAI, Azure OpenAI, and Ollama — so you can use whatever fits your company's policy, including on-premise models.

---

### Example skills included

The repo ships with 12 ready-to-use example skills:

- Code review with severity ratings
- Unit test generator (creates a new PR with the tests)
- Security scanner (OWASP Top 10, secrets, CVEs)
- Breaking change detector
- Dependency auditor (typosquatting, license issues)
- PR description enricher
- Changelog auto-updater
- Database migration safety reviewer
- Performance reviewer (N+1 queries, O(n²) patterns)
- And more

---

### Optional: observability via LangSmith

If you want to trace every agent decision (which tools it called, why, what it returned), you can connect a LangSmith project. Totally optional but really useful for debugging prompts.

---

### Links

- GitHub: [simonebittidev/azure-devops-skillflow](https://github.com/simonebittidev/azure-devops-skillflow)
- Azure DevOps Marketplace: search **SkillFlow** by SimoneBittiDev

Happy to answer questions about the architecture or how to write effective skill prompts. Would love feedback from anyone who tries it.

---

## Short version (for r/programming or a comment)

> I built **SkillFlow** — an Azure DevOps extension that runs LLM agents (Claude, GPT-4, Ollama) on every PR. You define what the agent does using plain Markdown files. Out of the box it handles code review, security scanning, test generation, changelog updates, and more. Open source, multi-provider, no custom scripting needed.
> GitHub: simonebittidev/azure-devops-skillflow

---

## Notes on posting

- Avoid posting the same text to multiple subreddits simultaneously — Reddit's spam filters flag cross-posts with identical content. Adapt the title slightly for each community.
- In r/devops and r/azuredevops, leading with the problem ("inconsistent code review") tends to perform better than leading with the solution.
- Add a comment with a concrete example (e.g. paste the security-scanner skill) shortly after posting — it drives engagement and shows the product in action.
- Flair the post as "Tool" or "Project" where available.
