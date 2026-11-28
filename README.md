# ![SPHinXsys Logo](assets/images/logo.png) SPHinXsim

**Project status**  
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/mit)
[![Linux](https://img.shields.io/badge/os-Linux-green.svg)](https://shields.io/)
[![Windows](https://img.shields.io/badge/os-Windows-green.svg)](https://shields.io/)
[![macOS](https://img.shields.io/badge/os-macOs-green.svg)](https://shields.io/)
![ci workflow](https://github.com/Xiangyu-Hu/SPHinXsim/actions/workflows/ci.yml/badge.svg?event=push)
[![Heterogeneous Computing](https://img.shields.io/badge/Heterogeneous-Computing-orange)](https://shields.io/)
[![UI CLI Interface](https://img.shields.io/badge/UI-CLI_Interface-orange)](https://shields.io/)
[![AI Native](https://img.shields.io/badge/AI-native-green)](https://shields.io/)


## Weakly open Q&A on-line session

You are welcome to join our **weekly online Q&A session** to discuss SPHinXsim, SPHinXsys, and related topics.

**Meeting details:**
- **When:** Every Friday at 12:00 PM (Germany local time, CET/CEST))
- **Duration:** 30 minutes or longer
- **Where:** Online via Microsoft Teams
- **Join the meeting:** [Microsoft Teams Meeting Link](https://teams.microsoft.com/meet/364917695711033?p=beQ12Ie0Hxrll1W5ND)

To help us prepare, please post your questions in advance on [GitHub Discussions](https://github.com/Xiangyu-Hu/SPHinXsim/discussions). Questions posted there will be prioritized during the session.

Each question has a suggested time limit of **5–7 minutes**. For topics requiring more in-depth discussion, we recommend opening a dedicated GitHub issue.

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

After following the [platform installation guide](docs/installation.md), install
the desired extras. On Linux and macOS:

```bash
python -m pip install -e '.[dev,cad,mcp,results,visualization]'
```

On Windows, the CI-tested installation omits the optional CAD extra:

```powershell
python -m pip install -e ".[dev,visualization]"
```

Then run:

```bash
sphinxsim --help
python -m sphinxsim --help
```

### Interface options

```bash
# CLI-first workflow (config.json as single source of truth)
sphinxsim capabilities --json
sphinxsim templates --json
sphinxsim init 2d/column_collapse project/config.json --root /workspace --json
sphinxsim validate project/config.json --root /workspace --json
sphinxsim results project/config.json --output-dir project/run_01 --root /workspace --json

# Interactive & agentic shell workflow
sphinxsim shell

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

#### For academic SPHinXsim users

If you are using SPHinXsim in your research, we would be grateful if you could cite our work in your publications. 
For this, we are happy to help you solve any issues you may encounter with SPHinXsim. Please contact us at <xiangyu.hu@tum.de>.


#### For SPHinXsim developers

If you don't have a GitHub account yet, please register for one. Fork the SPHinXsim repository to add new features or improve existing ones. Once your changes are ready, commit them and initiate a pull request to have your contributions merged into the main repository.

To ensure efficient and effective development, we prioritize addressing issues raised by active contributors—whether through code, documentation, or other means. We welcome any interaction with SPHinXsim and our team.

You can also join us as a collaborator, enabling you to branch directly within the main repository and review pull requests.

#### Developing with AI and external agents

We welcome responsible usage of AI and external agents in developing SPHinXsim. There are several principles to follow in order to ensure that the development process is efficient and effective:

1. **Incremental development**: Work with small, manageable changes and gradually build upon them. Each incremental changes should include testing and validation codes, and ensure full test suite carried out, to ensure that they work as intended and do not introduce any new issues or bugs. 

2. **Building context**: Before implementing new features or making changes, communicate with AI agents and other developers to
ensure that you have a clear understanding of the existing codebase and its architecture, especially on the context relevant to your changes.
This will help you avoid introducing unnecessary complexity and ensure that your contributions align with the overall design of the project.
During the development process, it is important to communicate with AI agents to ensure that the consistency between your intention and the agent's implementation.

3. **Consistency and integrity**: Asking AI agents whether your changes are consistent with the existing codebase or whether the new feature can be considered as an component within a larger system is crucial. This will help maintain the integrity of the project and ensure that your contributions are well-integrated with the existing codebase.

4. **Restraint and isolation**: Avoid making changes that are too broad or unrelated to the current development goals. Focus on the specific task at hand and ensure that your changes are well-defined and clearly documented.
Specifically, for new feature on the SPHinXsim c++ side, only newly added codes are permitted and they should be isolated in the designated folder `project`, except very small templated interface pieces. For detailed design and implementation, please refer to the [SPHinXsim design document](/project/README.md). Other changes on the SPHinXsim c++ side requires communication with maintainers and approval from the team.

5. **Testing and validation**: All changes should be thoroughly tested and validated to ensure that they work as intended and do not introduce any new issues or bugs. This includes running the full test suite and ensuring that all tests pass before submitting changes for review.

Together, we can build a leading-edge multi-physics simulator open for all!

If you have any further question, please contact <xiangyu.hu@tum.de>.
