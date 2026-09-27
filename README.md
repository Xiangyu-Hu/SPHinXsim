# ![SPHinXsys Logo](assets/images/logo.png) SPHinXsim Projects

This document explains the purpose of the repository, describes how to set up a project, and gives important guidance on branching, pull requests, and merges.

## Table of Contents
- [What is a SPHinXsim Project?](#what-is-a-sphinxsim-project)
- [How to setup a SPHinXsim Project](#how-to-setup-a-sphinxsim-project)
- [Branching, Pull Requests, and Merging](#branching-pull-requests-and-merging)
- [Contributing to a Project](#contributing-to-a-project)
- [Contributing to SPHinXsim](#contributing-to-sphinxsim)
- [Contributing to SPHinXsys](#contributing-to-sphinxsys)


## What is a SPHinXsim Project?

A **SPHinXsim Project** is a long‑term effort to simulate a complex, multi‑physics problem using SPHinXsim as the simulation environment.

### Ambitious, long‑term goal

A defining feature of such a project is that the ultimate objective is so challenging that there is always something meaningful left to add – more realistic physics, higher accuracy, or additional phenomena. The problem must therefore be an open scientific question (typically multi‑physics) that is under active investigation and will likely remain unsolved for the foreseeable future.

### Co-development with SPHinXsys and SPHinXsim

Another defining characteristic is that the project evolves together with two other projects:

- **SPHinXsys** – provides the numerical solvers as a C++ library.
- **SPHinXsim** – the software framework that lets you assemble and run complex simulations.

Development flows in both directions:

- **Bottom‑up**: new solvers and simulator features enable more physics to be included in the simulation.
- **Top‑down**: the need for new physics drives the creation of new solvers or simulator functionality.

## How to setup a SPHinXsim Project

Each project lives in its own branch, and the branch name must be the project name. All project‑specific files are stored in a directory called `project/` at the repository root. That directory has a fixed internal structure:

1. `extra_src/` – C++ source code that is needed to build the simulation but is not part of the core libraries.
   An umbrella header `sphinxsim_project.h` (or `sphinxsim_project.hpp` for template functions) must be provided; it should include all extra classes and functions.

2. `simulation/` – contains all (2d and 3d) simulation cases. Each case resides in a sub‑folder named after the case. A case folder includes the JSON configuration file and any assets (e.g., geometry files, initial conditions).

3. `docs/` – documentation for the project and auxiliary files (for example, “skills” definitions that enhance AI‑assisted development).

The repository includes a real project (`fish-swimming`) as an example to illustrate how
a project is composed of. However, please **DO NOT** branch your project from there.
You should still branch it from the `main` which already provides an empty template.  

## Branching, Pull Requests, and Merging

Although this repository is a fork of SPHinXsim, we maintain it independently.
Project branches are never pushed back to the upstream SPHinXsim repository.

To keep the main history clean and to control how upstream changes enter the projects, we use two special branches:

- `main` – tracks the upstream SPHinXsim. No direct contributions from project branches are allowed. It is the only branch that is allowed to receive updates from upstream. Note that the change to the templated project structure are made only by maintainers through a dedicated process, not via merging project branches.

All project branches must follow these rules:

- Create project branches exclusively from `main`.
- The only allowed merge from a shared branch is merging `main` into your project branch (to pick up template updates).
- Never merge a project branch directly into `main`.
- Never merge one project branch into another project branch.

This workflow ensures that:

- Project branches remain isolated from each other and from the upstream history.
- Upstream changes flow only through `main`, which is a clone of the `main` branch from the main SPHinXsim repository.

## Contributing to a Project

There are two ways to contribute to a project:

1. **Directly in the project branch** – if you are a core developer or a collaborator of the project, you can commit directly to the project branch. This is the simplest way to contribute. If you are not a core developer, you can request write access to the project branch from the project maintainers.
2. **Pull requests** – if you are not a core developer, you can fork the repository, create a branch from the project branch, and submit a pull request
to the corresponding project branch main project repository.
The project maintainers will review your changes and merge them into the project branch if they are accepted.

## Contributing to SPHinXsim

If you find that source code in `extra_src` is useful for other projects and you are considering contributing it to the main SPHinXsim repository. 

If you are a core developer or a collaborator of the SPHinXsim, you can commit directly a feature branch to SPHinXsim  in the main SPHinXsim repository and push your changes there without forking SPHinXsim repository. For this, you set the main SPHinXsim repository as a remote and push your changes to it.

Please note that, in this case, you you need to work on branches created from `main` and follow the SPHinXsim contribution guidelines.
Please **DO NOT** try to make pull request from the project branch.
Instead, the contribution needs to be moved from the `project/extra_src` folder to a proper subfolder of `sphinxsim` folder and equipped with its own tests and documentation other than those in the project.

It is essential to indicate from which project the contribution origins in the pull request summary. Otherwise the pull requested will not even be reviewed.

### Contributing to SPHinXsys

Again, if you are a core developer or a collaborator of SPHinXsys, you can commit directly to SPHinXsys. For this, you set the main SPHinXsys repository as a remote (different from the one for SPHinXsim).

The SPHinXsys code is embedded inside SPHinXsim at `sphinxsim/sphinxsys`.
You can use `git subtree split` to extract only the changes made in that subdirectory and push them to the SPHinXsys repository.”

Also in this case, please DO NOT try to make pull request from the project branch. The contribution or change needs to be restricted to the `sphinxsim/sphinxsys` folder and be equipped with its own tests and documentation other than those in the project or SPHinXsim. 