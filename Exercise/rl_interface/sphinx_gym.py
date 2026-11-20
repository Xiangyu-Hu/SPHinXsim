import json # Read, alter, and serialize the JSON simulation configuration.
import uuid # Generate unique IDs for separate episode output directories.
from pathlib import Path # Use pathlib for OS-independent path handling.

import gymnasium as gym # Import the standard reinforcement-learning environment API.
import numpy as np  # Use NumPy arrays for actions and observations.
from gymnasium import spaces # Import Gymnasium space definitions for validation and agent interoperability.

# Import SPHinXsim’s existing loader for dimension-specific native C++ modules.
from sphinxsim.bindings.loader import load_sphinxsys_core_nd

# Define a user-created Gymnasium environment, inheriting the standard gym.Env API.
class SPHinXGymEnv(gym.Env):
    """
    Gymnasium interface for reinforcement learning with SPHinXsim.

    The environment controls a two-dimensional SPH fluid simulation
    through the native Python/C++ bindings.

    Action:
        Uniform horizontal acceleration applied to WaterBody.

    Observation:
        Mean horizontal and vertical fluid-particle velocities.

    Reward:
        Negative squared error from the target horizontal velocity,
        with an additional quadratic control-effort penalty.
    """
    # Declare that this prototype does not implement a Gymnasium rendering mode.
    metadata = {"render_modes": []}

    # Constructor: configure the Gymnasium environment; the function signature continues below.
    def __init__(
        self,
        config_path, # Path to a JSON file describing the fluid simulation.
        dt_control=0.01, # Requested physical-time increment per environment step; not an exact guarantee of internal solver time.
        max_steps=5, # Maximum number of control decisions before truncating the episode.
    ):
        super().__init__()         # Initialize inherited Gymnasium environment behavior.

        self.config_path = Path(config_path).resolve()  # Resolve the config file to an absolute path.
        self.dt_control = float(dt_control) # Normalize user-specified control interval to a Python float.
        self.max_steps = int(max_steps) # Normalize episode horizon to an integer.

        # Reject nonpositive time intervals or episode lengths.
        if self.dt_control <= 0 or self.max_steps <= 0:
            raise ValueError("dt_control and max_steps must be positive")

        # Define the continuous admissible-action space for a single scalar control.
        self.action_space = spaces.Box(
            low=-0.5, # Minimum permitted horizontal control acceleration.
            high=0.5, # Maximum permitted horizontal control acceleration.
            shape=(1,), # Action must be a one-element vector, not a bare scalar.
            dtype=np.float32, # Match the Gymnasium observation-space dtype.
        )

        self.target_velocity = 0.03 # Set  target for mean horizontal fluid velocity.
        self.action_penalty = 0.001 # Set coefficient penalizing large control accelerations.

        # Define the expected numerical range, shape and dtype of observations.
        self.observation_space = spaces.Box(
            low=-np.inf, # No finite lower bound is imposed on observed mean velocity.
            high=np.inf, # No finite upper bound is imposed on observed mean velocity.
            shape=(2,), # Observation consists of two components: mean x and y velocities.
            dtype=np.float32, # Match the Gymnasium observation-space dtype.
        )

        self.core = load_sphinxsys_core_nd(2)   # Load the preexisting native 2D SPHinXsys Python extension.
        self.sim = None         # Release the environment’s reference to the previous simulation instance.
        self.step_count = 0  # Restart the Gymnasium decision counter at zero.

        # Begin construction of the temporary output root, relative to the working directory.
        self.work_root = (
            Path.cwd() / ".build-temp" / "gym_tests" # Store episode outputs in this directory under the current working directory.
        )
        self.work_root.mkdir(parents=True, exist_ok=True) # Create the output root and its parents if needed; allow it to already exist.

    # Private helper: extract a real physical observation from the active SPH simulation.
    def _get_obs(self):
        velocity = self.sim.getMeanVelocity("WaterBody") # Call the new native C++ method to obtain WaterBody’s mean particle velocity.

        # Convert the returned velocity vector into a NumPy array.
        return np.asarray(
            velocity,  # Input is the native C++/Eigen velocity vector exposed by pybind11.
            dtype=np.float32, # Match the Gymnasium observation-space dtype.
        ).copy() # Create an independent array, avoiding an unwanted shared view of native memory.

    # Gymnasium reset signature: start a new episode and return (observation, info).
    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed) # Initialize/reset Gymnasium’s Python-side random generator

        self.sim = None # Release the environment’s reference to the previous simulation instance.
        self.step_count = 0 # Restart the Gymnasium decision counter at zero.

        # Begin creating an episode-specific working directory name.
        episode_dir = (
            self.work_root / f"episode_{uuid.uuid4().hex[:12]}" # Use a truncated random UUID to avoid collisions among episode output folders.
        )
        episode_dir.mkdir(parents=True, exist_ok=False)    # Create a fresh folder; raise an exception if the chosen path already exists.

        config = json.loads(self.config_path.read_text()) # Read the source JSON configuration into a Python dictionary.
        config["solver_parameters"]["end_time"] = (  # Override configured end time to reflect the nominal episode horizon.
            self.dt_control * self.max_steps # Use requested interval times the maximum control-step count.
        )

        config_file = episode_dir / "config.json"         # Create an independent per-episode configuration filename.
        config_file.write_text(json.dumps(config, indent=2)) # Save the modified configuration as readable JSON.

        sim = self.core.SPHSimulation(str(config_file)) # Instantiate a native C++ SPHSimulation using that episode config.
        sim.resetOutputRoot(str(episode_dir / "simulation")) # Redirect SPH output directories into this episode’s own folder.

        sim.buildGeometries() # Construct the fluid and wall geometries from the JSON model.
        sim.generateParticles()  # Generate or load SPH particles according to the model.
        sim.buildSimulation()  # Build particle relations, dynamics, and the SPH solver.
        sim.initializeSimulation() # Run C++ initialization hooks before time integration.

        self.sim = sim  # Store the initialized native simulation for repeated step() calls.

        # Return the initial mean-velocity observation in this case and a metadata dictionary.
        return self._get_obs(), {
            "episode_dir": str(episode_dir), # Tell the caller where this episode’s generated files are stored.
        }

    # Gymnasium step: apply control, integrate SPH, measure the state, and calculate reward.
    def step(self, action):
        if self.sim is None: # Reject step() calls before an initial reset() or after close().
            raise RuntimeError("Call reset() before step().")

        if self.step_count >= self.max_steps: # Reject any extra action after the configured episode length.
            raise RuntimeError("Episode finished; call reset().")

        action = np.asarray(action, dtype=np.float32) # Convert user/agent input to the required NumPy float32 action type.

        if not self.action_space.contains(action): # Validate action shape, bounds, and dtype against the Gymnasium Box.
            raise ValueError(f"Invalid action: {action}")

        # 1. Receive the RL action.
        ax = float(action[0])

        # 2. Apply physical control in the native C++ solver.
        self.sim.setControlAcceleration("WaterBody", ax)

        # 3. Advance the actual SPH simulation in a time interval defined by self.dt_control
        self.sim.stepBy(self.dt_control)
        self.step_count += 1 # Count this completed control decision.

        # 4. Read the physical observation,i.e. physical mean velocity after the solver advances in this case.
        observation = self._get_obs()

        # 5. Compute reward from the physical flow state after the solver advances.
        vx = float(observation[0])

        reward = (
            -(vx - self.target_velocity) ** 2 # Penalize squared deviation between actual and desired x velocity.
            - self.action_penalty * ax ** 2 # Penalize squared control acceleration, discouraging unnecessarily strong actions.
        )

        terminated = False # There is no physical terminal condition implemented in this case.
        truncated = self.step_count >= self.max_steps # End the episode because of the step-count limit, not physical termination.

        # Build optional diagnostics returned alongside observation and reward.
        info = {
            "step_count": self.step_count,  # Report the number of completed control decisions.
            "physical_time": np.array(   # Represent actual solver physical time as a NumPy array for tolerant Gymnasium equivalence checks.
                [self.sim.getPhysicalTime()], # Read native time; one-element array retains original double precision.
                dtype=np.float64,
            ),
            "mean_vx": vx, # Provide mean horizontal velocity as diagnostic metadata.
            "control_ax": ax, # Provide the action actually submitted to the C++ solver.
        }

        # Return the five standard Gymnasium step values in their required order.
        return (
            observation, # Observed (mean_vx, mean_vy) vector.
            float(reward), # Scalar reward as an ordinary Python float.
            terminated,
            truncated,
            info, # Additional diagnostics dictionary.
        )

    # Release this Gymnasium environment’s reference to the native simulation.
    def close(self):
        self.sim = None  # Release the environment’s reference to the previous simulation instance.
