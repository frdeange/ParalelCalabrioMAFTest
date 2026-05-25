"""Variant v8 — Publish *minimal shell* PromptAgent versions.

Why this exists
---------------
``main_v8.py`` runs the same 3-agent sequential pipeline as v7, but uses
``FoundryAgent`` instead of ``Agent`` + ``FoundryChatClient`` so the agents
stay visible in Foundry Studio. To keep the comparison apples-to-apples with
v7, we want the PromptAgent definitions to be *thin shells*: only the model
binding, no server-side tools, no server-side instructions, no server-side
response_format / structured_inputs. Everything is supplied client-side at
runtime via ``options`` and inline system messages (exactly like v7).

This isolates one variable: ``FoundryAgent`` vs ``Agent + FoundryChatClient``.

Run
---
::

    python update_agents_v8.py            # publish all three
    python update_agents_v8.py intent
    python update_agents_v8.py sql
    python update_agents_v8.py executor

After running, ``main_v8.py`` auto-picks the latest version via
``list_versions``. Run ``update_agents.py`` later to restore the hosted-MCP
variants used by main_v2 / main_v3.
"""

from __future__ import annotations

import os
import sys
from typing import Callable

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import AgentDefinition, PromptAgentDefinition

load_dotenv()

MODEL = "gpt-5.2"


def _shell_definition() -> PromptAgentDefinition:
    """A minimal PromptAgent: just a model binding, nothing else."""
    return PromptAgentDefinition(model=MODEL, tools=[])


AGENTS: dict[str, tuple[str, Callable[[], AgentDefinition]]] = {
    "intent": ("wfm-intent-classifier", _shell_definition),
    "sql": ("wfm-sql-builder", _shell_definition),
    "executor": ("wfm-query-executor", _shell_definition),
}


def main() -> None:
    args = sys.argv[1:]
    targets = args if args else list(AGENTS.keys())

    unknown = [t for t in targets if t not in AGENTS]
    if unknown:
        print(f"Unknown target(s): {unknown}. Valid: {list(AGENTS.keys())}")
        sys.exit(2)

    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    credential = DefaultAzureCredential()

    print("Publishing minimal-shell PromptAgent versions for v8 ...")
    with AIProjectClient(endpoint=endpoint, credential=credential) as client:
        for key in targets:
            agent_name, factory = AGENTS[key]
            print(f"Updating {agent_name} ...")
            new_version = client.agents.create_version(
                agent_name=agent_name,
                definition=factory(),
            )
            print(f"  -> {new_version.id} (version {new_version.version})")
    print(
        "Done. main_v8.py auto-picks the latest version. "
        "Re-run update_agents.py to restore the hosted-MCP definitions."
    )


if __name__ == "__main__":
    main()
