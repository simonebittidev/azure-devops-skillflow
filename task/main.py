#!/usr/bin/env python3
"""Entry point for the SkillFlow Azure DevOps Pipeline Task.

Discovers all Skill .md files in .azdo/skills/ (or a configured directory),
then runs each one as a LangGraph agent against the current Pull Request.

Azure DevOps injects pipeline context automatically as environment variables:
    SYSTEM_COLLECTIONURI              — organization URL
    SYSTEM_TEAMPROJECT                — project name
    BUILD_REPOSITORY_ID               — repository ID
    SYSTEM_PULLREQUEST_PULLREQUESTID  — pull request ID
    SYSTEM_ACCESSTOKEN                — pipeline OAuth token

Task inputs (optional, from task.json):
    INPUT_SKILLSDIR  — directory to scan for .md skill files (default: .azdo/skills)
    INPUT_VERBOSE    — enable verbose logging (default: false)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _get_env(var: str, default: str = "") -> str:
    return os.environ.get(var, default).strip()


def _require_env(var: str, description: str) -> str:
    value = _get_env(var)
    if not value:
        print(f"##vso[task.logissue type=error]Missing required environment variable: {var} ({description})")
        print(f"##vso[task.logissue type=error]This variable is set automatically by Azure DevOps. Make sure the task runs inside a PR pipeline.")
        sys.exit(1)
    return value


def main() -> None:
    # --- AzDO pipeline context (injected automatically, no user config needed) ---
    org_url = _require_env("SYSTEM_COLLECTIONURI", "Azure DevOps organization URL")
    project = _require_env("SYSTEM_TEAMPROJECT", "Azure DevOps project name")
    repository_id = _require_env("BUILD_REPOSITORY_ID", "Git repository ID")
    pull_request_id_str = _require_env("SYSTEM_PULLREQUEST_PULLREQUESTID", "Pull Request ID")
    access_token = _require_env("SYSTEM_ACCESSTOKEN", "Pipeline OAuth token")

    # --- Optional task inputs ---
    skills_dir = _get_env("INPUT_SKILLSDIR", ".azdo/skills")
    verbose = _get_env("INPUT_VERBOSE", "false").lower() == "true"

    # Validate PR ID
    if not pull_request_id_str.isdigit():
        print(f"##vso[task.logissue type=error]SYSTEM_PULLREQUEST_PULLREQUESTID is not a valid number: '{pull_request_id_str}'")
        print("##vso[task.logissue type=error]Make sure this pipeline is triggered by a Pull Request.")
        sys.exit(1)

    pull_request_id = int(pull_request_id_str)

    # Add task directory to Python path so the `src` package is importable
    task_dir = Path(__file__).parent
    sys.path.insert(0, str(task_dir))

    from src.skill_loader import load_skill, SkillLoadError
    from src.models.skill import PRContext
    from src.agent import run_skill

    # --- Discover skill files ---
    skills_path = Path(skills_dir)
    if not skills_path.exists():
        print(f"##vso[task.logissue type=warning]Skills directory not found: '{skills_dir}'")
        print("##vso[task.logissue type=warning]Create .md skill files in that directory to use SkillFlow.")
        print("##vso[task.complete result=Succeeded]No skills to run.")
        return

    skill_files = sorted(skills_path.glob("*.md"))
    if not skill_files:
        print(f"##vso[task.logissue type=warning]No .md skill files found in '{skills_dir}'.")
        print("##vso[task.complete result=Succeeded]No skills to run.")
        return

    print(f"##[section]SkillFlow — found {len(skill_files)} skill(s) in '{skills_dir}'")
    for f in skill_files:
        print(f"  • {f.name}")

    # --- Build PR context (shared across all skills) ---
    ctx = PRContext(
        organization_url=org_url,
        project=project,
        repository_id=repository_id,
        pull_request_id=pull_request_id,
        access_token=access_token,
    )

    # --- Run each skill ---
    errors: list[str] = []

    for skill_file in skill_files:
        print(f"\n##[section]Loading skill: {skill_file.name}")

        try:
            skill = load_skill(skill_file)
        except SkillLoadError as exc:
            msg = f"Failed to load '{skill_file.name}': {exc}"
            print(f"##vso[task.logissue type=error]{msg}")
            errors.append(msg)
            continue

        print(f"  Provider: {skill.provider} / {skill.model}")
        print(f"  Output:   {skill.output}")
        print(f"  Tools:    {', '.join(skill.tools)}")

        print(f"\n##[section]Running skill: {skill.name}")
        try:
            result = run_skill(skill, ctx, verbose=verbose)
            print(result)
        except Exception as exc:
            msg = f"Skill '{skill.name}' failed: {exc}"
            print(f"##vso[task.logissue type=error]{msg}")
            if verbose:
                import traceback
                traceback.print_exc()
            errors.append(msg)

    # --- Final result ---
    if errors:
        print(f"\n##vso[task.logissue type=error]{len(errors)} skill(s) failed.")
        sys.exit(1)

    print(f"\n##[section]SkillFlow completed — {len(skill_files)} skill(s) ran successfully.")
    print("##vso[task.complete result=Succeeded]")


if __name__ == "__main__":
    main()
