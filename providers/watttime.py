"""WattTime marginal-emissions provider (optional, token-based).

Marginal emissions (MOER) reflect the generator that responds to an incremental
change in load, the right signal for deciding WHEN to shift flexible compute,
which average intensity cannot capture. WattTime's free tier exposes the
``co2_moer`` signal index: a 0-100 percentile of how clean the margin is right
now relative to the last two weeks, for the CAISO_NORTH region. Other regions
need a WattTime Pro subscription. A LOW percentile means the margin is relatively
clean, a good time to run.

Register at https://watttime.org/ and pass watttime_username / watttime_password.
The token from /login expires after 30 minutes, which is fine for a one-shot CI
check.
"""

import base64

from providers import base

BASE = "https://api.watttime.org"
DEFAULT_REGION = "CAISO_NORTH"


def login(username, password):
    """Exchange WattTime credentials for a bearer token, or None on failure."""
    if not username or not password:
        return None
    raw = f"{username}:{password}".encode()
    header = "Basic " + base64.b64encode(raw).decode()
    data = base.request(f"{BASE}/login", headers={"Authorization": header}, parse="json")
    if not data:
        return None
    return data.get("token")


def get_marginal_index(region, token):
    """Return the latest marginal co2_moer percentile (0-100) for region, or None.

    Lower is cleaner. Uses WattTime's free signal-index endpoint. The response is
    parsed tolerantly so a minor shape change does not break the integration.
    """
    if not token:
        return None
    url = f"{BASE}/v3/signal-index?region={region}&signal_type=co2_moer"
    data = base.request(url, headers={"Authorization": f"Bearer {token}"}, parse="json")
    if not data:
        return None

    value = None
    points = data.get("data")
    if isinstance(points, list) and points:
        value = points[0].get("value")
    elif data.get("value") is not None:
        value = data.get("value")
    elif data.get("percent") is not None:  # v2-style fallback
        value = data.get("percent")

    if value is None:
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None
