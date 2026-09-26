"""Detached local worker entry point; UI processes never own the solver lifetime."""
import sys
from .runtime import AgentRuntime
from .run_manager import RunManager

if __name__ == "__main__":
    project, workspace, workflow, action, token = sys.argv[1:]
    RunManager(AgentRuntime(project, workspace)).run(workflow, action, token)
