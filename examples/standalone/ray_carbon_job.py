"""Example: carbon-aware Ray batch job.

The driver waits until the grid is clean, then submits the Ray tasks. If no clean
window opens before the deadline, run_when_clean raises so the scheduler retries
rather than running the heavy job on dirty power.

    pip install ray
"""

import ray

from integrations.ray_carbon import run_when_clean


@ray.remote
def train_shard(shard):
    return f"trained {shard}"


def run_batch():
    ray.init()
    futures = [train_shard.remote(i) for i in range(8)]
    return ray.get(futures)


if __name__ == "__main__":
    results = run_when_clean(run_batch, zones="auto:green", max_carbon=200)
    print(results)
