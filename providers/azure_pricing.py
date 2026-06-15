"""Azure Retail Prices lookup for cost-aware zone selection.

Uses the public Azure Retail Prices API (no auth, no key) to fetch a
representative on-demand Linux VM hourly price per region, so the dispatcher can
pick a zone that is both clean and cheap. The price is a *relative* signal for
ranking regions against each other; it isn't a billing quote. Results are memoized
per region for the life of the process.
"""

from urllib.parse import quote

from providers import base

PRICES_API = "https://prices.azure.com/api/retail/prices"
# Representative general-purpose VM whose price tracks regional cost differences.
REP_SKU = "Standard_D2s_v5"

_cache: dict = {}


def get_region_price(arm_region):
    """Return a representative USD/hour VM price for an Azure region, or None."""
    if not arm_region:
        return None
    if arm_region in _cache:
        return _cache[arm_region]

    odata = (
        f"armRegionName eq '{arm_region}' and armSkuName eq '{REP_SKU}' "
        "and priceType eq 'Consumption' and serviceName eq 'Virtual Machines'"
    )
    url = f"{PRICES_API}?$filter={quote(odata)}&currencyCode=USD"
    data = base.request(url, parse="json")

    price = None
    if data:
        for item in data.get("Items", []):
            # Want the base Linux on-demand rate; skip Windows/spot/low-priority
            tag = (item.get("productName") or "") + (item.get("skuName") or "")
            if "Windows" in tag or "Spot" in tag or "Low Priority" in tag:
                continue
            retail = item.get("retailPrice")
            if retail:
                price = float(retail)
                break

    _cache[arm_region] = price
    return price
