#!/usr/bin/env python3
"""Entry point for the Azure DevOps Pipeline Task.

Reads inputs from environment variables (set by the AzDO pipeline runtime),
loads the Skill file, and runs the LangGraph agent against the Pull Request.

Environment variables (set automatically by AzDO from task.json inputs):
    INPUT_SKILLFILE         — path to the .md skill file
    INPUT_AZUREDEVOPSURL    — organization URL
    INPUT_PROJECTNAME       — project name
    INPUT_REPOSITORYID      — repository ID
    INPUT_PULLREQUESTID     — pull request ID (integer)
    INPUT_ACCESSTOKEN       — PAT / System.AccessToken
    INPUT_VERBOSE           — "true" or "false"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _get_input(name: str, required: bool = True) -> str:
    """Read a task input from the environment.

    Azure DevOps Pipeline Tasks expose inputs as environment variables
    named INPUT_<UPPERCASED_INPUT_NAME>.
    """
    env_key = f"INPUT_{name.upper()}"
    value = os.environ.get(env_key, "").strip()
    if required and not value:
        print(f"##vso[task.logissue type=error]Missing required input: {name} (env: {env_key})")
        sys.exit(1)
    return value


def main() -> None:
    skill_file = _get_input("skillFile")
    org_url = _get_input("azureDevOpsUrl")
    project = _get_input("projectName")
    repository_id = _get_input("repositoryId")
    pull_request_id_str = _get_input("pullRequestId")
    access_token = _get_input("accessToken")
    verbose = _get_input("verbose", required=False).lower() == "true"

    # Validate PR ID is numeric
    if not pull_request_id_str.isdigit():
        print(
            f"##vso[task.logissue type=error]pullRequestId must be a number, got: {pull_request_id_str}"
        )
        sys.exit(1)

    pull_request_id = int(pull_request_id_str)

    # Add task directory to Python path so `src` package is importable
    task_dir = Path(__file__).parent
    sys.path.insert(0, str(task_dir))

    from src.skill_loader import load_skill, SkillLoadError
    from src.models.skill import PRContext
    from src.agent import run_skill

    # Load skill
    print(f"##[section]Loading skill from: {skill_file}")
    try:
        skill = load_skill(skill_file)
    except SkillLoadError as exc:
        print(f"##vso[task.logissue type=error]{exc}")
        sys.exit(1)

    print(f"Skill loaded: '{skill.name}' (provider: {skill.provider}, model: {skill.model})")
    print(f"Output type: {skill.output} | Tools: {', '.join(skill.tools)}")

    # Build PR context
    ctx = PRContext(
        organization_url=org_url,
        project=project,
        repository_id=repository_id,
        pull_request_id=pull_request_id,
        access_token=access_token,
    )

    # Run the skill
    print(f"##[section]Running skill '{skill.name}' on PR #{pull_request_id}")
    try:
        result = run_skill(skill, ctx, verbose=verbose)
    except Exception as exc:
        print(f"##vso[task.logissue type=error]Skill execution failed: {exc}")
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    print("##[section]Skill completed successfully")
    print(result)
    print("##vso[task.complete result=Succeeded]")


if __name__ == "__main__":
    main()
