# Instructions for developing C++ SPHinXsim simulator

All C++ development from the SPHinXsim contributor need to go through a two-step pattern.

- First, the new feature is added through small project in `project/`.
- Second, after a successful project the new new feature will be moved into `sphinxsim/sph_simulation/`.

## How to setup a small SPHinXsim Project

Each C++ development project lives in its own branch. All project‑specific files are stored in a directory called `project/` at the repository root. That directory has a fixed internal structure:

1. `extra_src/` – C++ source code that is needed to build the simulation but is not part of the core libraries.
   An umbrella header `sphinxsim_project.h` (or `sphinxsim_project.hpp` for template functions) must be provided; it should include all extra classes and functions.

2. `simulation/` – contains all (2d and 3d) simulation cases. Each case resides in a sub‑folder named after the case. A case folder includes the JSON configuration file and any assets (e.g., geometry files, initial conditions).

3. `docs/` – documentation for the project and auxiliary files (for example, “skills” definitions that enhance AI‑assisted development).

4. Interface to `sphinxsim/sph_simulation/` - In all cases, only small interface patch is allowed to add to the origin files. Generally two types with `SPHINXSIM_PROJECT` macro. One including the `sphinxsim_project.h` or `sphinxsim_project.hpp` header. Another is the call to a function defined in the header.

5. Exception - One need contact the maintainer if anything above can not be followed.

## Two-step pull requests

According the two-step approach, a pull request ready for review should be in small project form. 

- The pull request will be reviewed and changes are request until the project itself is OK.

- Then, the contribution will be requested to be moved into `sphinxsim/sph_simulation/` and clean `project/` so that only empty functions left in `sphinxsim_project.h` or `sphinxsim_project.hpp` header are kept. So the other project can be started from blank.

- The tests for the project will be moved to `tests/` in their proper sub-directories.

- Bug fix and other local enhancement will still follow the usual one-step approach.