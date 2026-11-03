"""Default image project, replaced by the team's normal Synapse deployment."""

from pathlib import Path

from synapse.core.synapse import Synapse

if __name__ == "__main__":
    Synapse.createAndRunRuntime(root=Path.cwd())
