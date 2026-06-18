"""Carbon-aware inference routing: send each async request to the cleanest region.

For latency-tolerant inference (batch, async, background), route to the endpoint
whose grid is cleanest right now. Re-rank periodically (intensity shifts hourly),
not per request, to avoid hammering the grid APIs.

    pip install carbon-aware-dispatcher
"""

from integrations.inference_router import cleanest_endpoint, rank_endpoints

ENDPOINTS = [
    {"name": "us-west", "zone": "CISO", "url": "https://us-west.example/infer"},
    {"name": "france", "zone": "FR", "url": "https://eu-fr.example/infer"},
    {"name": "norway", "zone": "NO-NO1", "url": "https://eu-no.example/infer"},
]


def main():
    ranking = rank_endpoints(ENDPOINTS)
    print("Endpoints by live carbon intensity (cleanest first):")
    for e in ranking:
        intensity = e["intensity"] if e["intensity"] is not None else "n/a"
        print(f"  {e['name']:<8} {e['zone']:<8} {intensity} gCO2eq/kWh")

    target = cleanest_endpoint(ENDPOINTS)
    if target:
        print(f"\nRoute inference to: {target['name']} ({target['url']})")
        # requests.post(target["url"], json=payload)
    else:
        print("\nNo endpoint had a live reading; fall back to your default.")


if __name__ == "__main__":
    main()
