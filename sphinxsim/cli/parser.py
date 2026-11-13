"""Argument parser and shell completion definitions for the SPHinXsim CLI."""

from __future__ import annotations

import argparse

from sphinxsim.commands import register as register_structured_commands

from .common import __version__
from .explore import cmd_explore
from .generate import cmd_generate
from .run import cmd_run
from .shell import cmd_shell
from .update import cmd_update
from .validate import cmd_validate
from .main import cmd_preview


COMPLETION_SCRIPTS = {
    "bash": """_sphinxsim() {
    local cur prevwords cword
    _init_completion || return
    COMPREPLY=( $(COMP_WORDS[0] --generate-completion bash 2>/dev/null) )
}
complete -F _sphinxsim sphinxsim
""",
    "zsh": """#compdef sphinxsim

_sphinxsim() {
    local -a commands
    commands=(
        'generate:Generate simulation config'
        'validate:Validate JSON simulation config'
        'update:Update existing simulation config'
        'run:Run simulation from JSON config'
        'preview:Render interactive geometry/BC preview'
        'explore:Ask schema/functionality questions'
        'shell:Interactive shell workflow'
        'capabilities:Inspect schema and native routing evidence'
        'compile:Compile quantities into native configuration'
        'geometry:Generate verified CAD geometry'
        'project:Inspect project inputs'
        'job:Query or control an explicit job'
        'desktop:Serve or operate the native application'
        'mcp:Serve native operations over MCP stdio'
    )
    _describe 'sphinxsim commands' subcmds
}
_sphinxsim "$@" """,
    "fish": """complete -c sphinxsim -f -a \"generate\" -d \"Generate simulation config\"
complete -c sphinxsim -f -a \"validate\" -d \"Validate JSON simulation config\"
complete -c sphinxsim -f -a \"update\" -d \"Update existing simulation config\"
complete -c sphinxsim -f -a \"run\" -d \"Run simulation from JSON config\"
complete -c sphinxsim -f -a \"preview\" -d \"Render interactive geometry/BC preview\"
complete -c sphinxsim -f -a \"explore\" -d \"Ask schema/functionality questions\"
complete -c sphinxsim -f -a \"shell\" -d \"Interactive shell workflow\"
complete -c sphinxsim -f -a \"capabilities compile geometry project job desktop mcp\" -d \"Native application operations\" """,
}


class GenerateCompletionAction(argparse.Action):
    """Custom argparse action to print shell auto-completion scripts and exit."""

    def __call__(self, parser, namespace, values, option_string=None):
        shell = values.lower()
        if shell in COMPLETION_SCRIPTS:
            print(COMPLETION_SCRIPTS[shell].strip())
        parser.exit(0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sphinxsim",
        description="Python UI for the SPHinXsys multi-physics C++ library.",
    )
    parser.add_argument("--version", action="version", version=f"sphinxsim {__version__}")

    parser.add_argument(
        "--generate-completion",
        choices=["bash", "zsh", "fish"],
        action=GenerateCompletionAction,
        help="Generate shell auto-completion script for bash, zsh, or fish.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    gen = subparsers.add_parser(
        "generate",
        help="Generate a simulation config from a natural-language description.",
    )
    gen.add_argument("description", help="Natural-language simulation description.")
    gen.add_argument(
        "-o", "--output", metavar="FILE", default=None, help="Write JSON config to FILE instead of stdout."
    )
    gen.set_defaults(func=cmd_generate)

    val = subparsers.add_parser(
        "validate", help="Validate a JSON simulation config against the schema."
    )
    val.add_argument("config_file", nargs='?', default="config.json", help="Path to JSON config file.")
    val.set_defaults(func=cmd_validate)

    upd = subparsers.add_parser(
        "update",
        help="Update an existing simulation config from a natural-language instruction.",
    )
    upd.add_argument("config_file", help="Path to an existing JSON config file.")
    upd.add_argument("description", help="Natural-language update instruction.")
    upd.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        default=None,
        help="Write updated JSON to FILE instead of updating in place.",
    )
    upd.add_argument(
        "--patch-mode",
        action="store_true",
        help="Use operation-based patch updates (provider must support update_patch()).",
    )
    upd.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview patch-mode results without writing output file.",
    )
    upd.add_argument(
        "--strict",
        choices=["true", "false"],
        default="true",
        help="Strict patch application behavior for --patch-mode (default: true).",
    )
    upd.set_defaults(func=cmd_update)

    run = subparsers.add_parser("run", help="Run a simulation from a JSON config file.")
    run.add_argument("config_file", nargs='?', default="config.json", help="Path to JSON config file.")
    run.set_defaults(func=cmd_run)

    prev = subparsers.add_parser(
        "preview",
        help="Render an interactive geometry/BC preview of a JSON config file.",
    )
    prev.add_argument(
        "config_file",
        nargs="?",
        default="config.json",
        help="Path to JSON config file.",
    )
    prev.add_argument(
        "--with-particles",
        action="store_true",
        help="Also run particle generation and overlay the latest generated particles per body.",
    )
    prev.add_argument(
        "--off-screen",
        action="store_true",
        help="Render off-screen (no window). Useful for automated testing.",
    )
    prev.add_argument(
        "--screenshot",
        "-s",
        default=None,
        help="Save a screenshot to this file path instead of opening an interactive window.",
    )
    prev.set_defaults(func=cmd_preview)

    exp = subparsers.add_parser(
        "explore",
        help="Ask schema/functionality questions using the configured LLM provider.",
    )
    exp.add_argument("question", help="Question about the simulator schema or functionality.")
    exp.set_defaults(func=cmd_explore)

    shell = subparsers.add_parser(
        "shell",
        help="Interactive shell for config load/modify/validate workflow.",
    )
    shell.set_defaults(func=cmd_shell)

    register_structured_commands(subparsers)
    return parser


__all__ = ["COMPLETION_SCRIPTS", "GenerateCompletionAction", "_build_parser"]
