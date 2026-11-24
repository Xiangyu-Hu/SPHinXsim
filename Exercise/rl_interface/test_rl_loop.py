from pathlib import Path # Import Path, which is used to construct and manipulate file paths.
import numpy as np # Import NumPy for creating numerical arrays and checking numerical values.

from sphinx_gym import SPHinXGymEnv # Import the custom Gymnasium environment implemented in sphinx_gym.py.

# Get the absolute path of this Python script.
# __file__ refers to the current file, test_rl_loop.py.
# .resolve() converts its path into an absolute path.
# .parents[2] moves two parent levels upward to the SPHinXsim repository root.
root = Path(__file__).resolve().parents[2]

# Construct the path to the JSON configuration file for the SPH simulation.
config = (
    root / "examples/input/test_simulation_2d/config_smoke.json"
)

# Create an instance of the custom SPHinXsim Gymnasium environment.
env = SPHinXGymEnv(
    config_path=config,  # Specify the JSON file describing the physical simulation.
    dt_control=0.01,  # Request a physical-time advancement of 0.01 per control step.
    max_steps=5,  # Allow at most five control actions in one episode.
)

# Define a predetermined sequence of five control actions.
actions = [
    [+0.5],
    [+0.5],
    [-0.5],
    [0.0],
    [+0.2],
]

# Use try/finally to ensure that the simulation environment is closed,
# even if an exception occurs during the test.
try:
    obs, info = env.reset(seed=42)  # Start a new Gymnasium episode and initialize a new SPH simulation.

    print("Initial observation:", obs) # Display the initial mean fluid velocity.

    total_reward = 0.0  # Initialize the cumulative reward of this episode.
    previous_time = 0.0  # Store the previous physical time for checking time progression.


    # Iterate through the five predefined actions.
    # enumerate(actions, start=1) produces:
    # (1, [+0.5]), (2, [+0.5]), ..., (5, [+0.2]).
    # i is the control-step number.
    # values is the one-element list containing the acceleration.
    for i, values in enumerate(actions, start=1):
        # Convert the current action list into a NumPy float32 array.
        # Example: [+0.5] becomes array([0.5], dtype=float32).
        # This matches the Gymnasium action_space shape and data type.
        action = np.array(values, dtype=np.float32)

        # Execute one full Gymnasium interaction step defined in sphinx_gym.py.
        obs, reward, terminated, truncated, info = (
            env.step(action)
        )

        total_reward += reward # Add the reward from this step to the episode's total reward.

        physical_time = float(info["physical_time"][0]) # Read the actual physical time from the information dictionary.

        # Display the numerical results of the current control step.
        print(
            f"Step {i}: "
            f"action={action[0]:+.2f}, "
            f"vx={obs[0]:+.8f}, " # Print the mean horizontal fluid velocity with eight decimals.
            f"vy={obs[1]:+.8f}, "  # Print the mean vertical fluid velocity with eight decimals.
            f"reward={reward:+.8f}, "
            f"time={physical_time:.6f}"
        )

        # Verify that the returned observation belongs to the
        # observation space defined by the Gymnasium environment.
        # This checks its shape, data type, and allowed bounds.
        assert env.observation_space.contains(obs)

        assert np.isfinite(reward) # Verify that the reward is a finite numerical value.
        assert physical_time > previous_time # Verify that the simulation's actual physical time has increased.

        previous_time = physical_time # Save the current time for comparison with the next control step.

        if terminated or truncated:
            break

    assert truncated # Verify that the episode ended because it reached its step limit.
    print("Total episode reward:", total_reward)
    print("FULL RL-CFD INTERACTION TEST PASSED")

finally:
    env.close() # Release the environment's reference to the native SPH simulation.
