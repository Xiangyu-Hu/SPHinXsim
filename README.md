# ![SPHinXsys Logo](assets/images/logo.png) SPHinXsim

**Project status**  
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/mit)
[![Linux](https://img.shields.io/badge/os-Linux-green.svg)](https://shields.io/)
[![Windows](https://img.shields.io/badge/os-Windows-green.svg)](https://shields.io/)
[![macOS](https://img.shields.io/badge/os-macOs-green.svg)](https://shields.io/)
![ci workflow](https://github.com/Xiangyu-Hu/SPHinXsim/actions/workflows/ci.yml/badge.svg?event=push)
[![Heterogeneous Computing](https://img.shields.io/badge/Heterogeneous-Computing-orange)](https://shields.io/)
[![UI CLI Interface](https://img.shields.io/badge/UI-CLI_Interface-orange)](https://shields.io/)

## Repository Description

SPHinXsim is the agent-ready simulation platform for [SPHinXsys](https://github.com/Xiangyu-Hu/SPHinXsys): a structured, automation-friendly interface for building, validating, and running high-fidelity multi-physics simulations.

From a single specification, users and external agents can validate inputs, generate CAD geometry, compile native configurations, launch simulations, and inspect verified results through one consistent CLI and MCP workflow.

### Built for people and autonomous workflows

* JSON-first simulation design with a single source of truth
* Native CLI, shell, and MCP interfaces for interactive and automated use
* CAD-driven geometry generation and validation before execution
* Optional Python integration via pybind11 for embedded workflows
* Visualization and result inspection with [PyVista](https://pyvista.org/)
* Heterogeneous CPU/GPU execution with [SYCL](https://www.khronos.org/sycl/)

### SPHinXsim Agent

The newest workflow makes `sphinxsim` itself the execution layer for both human operators and external agents. There is no separate proprietary agent runtime or cloud-only control plane; the same native application operations are exposed through the shell, the CLI, and the Model Context Protocol (MCP).

This enables powerful agent-driven workflows such as:

* schema validation and simulation input checking,
* CAD generation and geometry review,
* compilation of native simulation configurations,
* persistent execution and job management,
* result inspection and verified resource publication.

For the complete workflow, installation guidance, and MCP configuration, see [Native CLI and external agents](docs/agent-workflows.md).

## Quick start

```bash
python -m pip install -e '.[dev,cad,mcp,results,visualization]'
sphinxsim --help
python -m sphinxsim --help
```

### Interface options

```bash
# Shell / terminal workflow
sphinxsim capabilities --json
sphinxsim validate project/config.json --root /workspace --json

# Python / CLI entry point
python -m sphinxsim --help

# MCP / external agent workflow
sphinxsim mcp --root /workspace
```

## Installation, tutorials, and documentation

SPHinXsim is cross-platform and can be built and used on Windows, Linux, and macOS.

For installation guides, tutorials, and the project manual, visit <https://xiangyu-hu.github.io/SPHinXsim/>.

For native shell usage, CLI workflows, CAD generation, and optional MCP/agent integration, see [Native CLI and external agents](docs/agent-workflows.md).

## Interaction with SPHinXsim and the team

Thank you for using and supporting our open-source project!
We value each feedback.

#### For SPHinXsim users

Your input is crucial to us. We encourage you to report any issues you encounter with the library, including:

* Bug reports
* Interface improvement suggestions, such as API design, visualization, documentation, and user experience
* Feature requests
* General feedback on your experience with SPHinXsim

We particularly appreciate feedback stemming from practical simulations or projects, as these insights are essential for improving SPHinXsim.

#### For SPHinXsim developers

If you don't have a GitHub account yet, please register for one. Fork the SPHinXsim repository to add new features or improve existing ones. Once your changes are ready, commit them and initiate a pull request to have your contributions merged into the main repository.

To ensure efficient and effective development, we prioritize addressing issues raised by active contributors—whether through code, documentation, or other means. We welcome any interaction with SPHinXsim and our team.

You can also join us as a collaborator, enabling you to branch directly within the main repository and review pull requests.

Together, we can build a leading-edge multi-physics simulator open for all!

If you have any further question, please contact <xiangyu.hu@tum.de>.