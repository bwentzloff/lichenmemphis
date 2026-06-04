
"""Download Shelby County, TN iNaturalist observations for fungi, slime molds, lichens, and mosses.

This script uses the iNaturalist v1 API and stores all matching observations in a
local JSON file.
"""

from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import RemoteDisconnected
from datetime import datetime, timezone


BASE_URL = "https://api.inaturalist.org/v1"
REQUEST_TIMEOUT_SECONDS = 30
PER_PAGE = 200
OUTPUT_FILE = "shelby_county_tn_inat_observations.json"
MAX_REQUEST_RETRIES = 6
INITIAL_RETRY_DELAY_SECONDS = 2.0

# Bounding box for Shelby County, Tennessee area.
SHELBY_BOUNDS = {
    "swlat": 34.994,
    "swlng": -90.316,
    "nelat": 35.407,
    "nelng": -89.627,
}


def get_json(endpoint: str, params: dict[str, object]) -> dict:
    """Call an iNaturalist endpoint and return decoded JSON data."""
    query = urllib.parse.urlencode(params, doseq=True)
    url = f"{BASE_URL}{endpoint}?{query}"
    retry_delay = INITIAL_RETRY_DELAY_SECONDS

    for attempt in range(1, MAX_REQUEST_RETRIES + 1):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "bioindicators-script/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            is_retryable = error.code in {403, 408, 409, 429, 500, 502, 503, 504}
            if not is_retryable or attempt == MAX_REQUEST_RETRIES:
                raise

            retry_after = error.headers.get("Retry-After") if error.headers else None
            wait_seconds = retry_delay
            if retry_after:
                try:
                    wait_seconds = max(float(retry_after), retry_delay)
                except ValueError:
                    wait_seconds = retry_delay

            print(
                f"HTTP {error.code} from iNaturalist (attempt {attempt}/{MAX_REQUEST_RETRIES}). "
                f"Retrying in {wait_seconds:.1f}s..."
            )
            time.sleep(wait_seconds)
            retry_delay = min(retry_delay * 2, 60.0)
        except (
            urllib.error.URLError,
            RemoteDisconnected,
            ssl.SSLError,
            socket.timeout,
            TimeoutError,
            ConnectionError,
            EOFError,
        ) as error:
            if attempt == MAX_REQUEST_RETRIES:
                raise

            print(
                f"Network error from iNaturalist ({error}) "
                f"(attempt {attempt}/{MAX_REQUEST_RETRIES}). Retrying in {retry_delay:.1f}s..."
            )
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60.0)

    raise RuntimeError("Exhausted retries while calling iNaturalist API")


def resolve_taxon_id(query: str, preferred_name_contains: str | None = None) -> int:
    """Find a taxon id from an iNaturalist taxon search query."""
    data = get_json("/taxa/autocomplete", {"q": query, "per_page": 30})
    results = data.get("results", [])
    preferred_name_contains_lc = (
        preferred_name_contains.lower() if preferred_name_contains else None
    )

    for taxon in results:
        name = (taxon.get("name") or "").lower()
        preferred_name = (taxon.get("preferred_common_name") or "").lower()

        if preferred_name_contains_lc:
            if preferred_name_contains_lc in preferred_name or preferred_name_contains_lc in name:
                return int(taxon["id"])
        elif query.lower() == name:
            return int(taxon["id"])

    if results:
        return int(results[0]["id"])

    raise RuntimeError(f"Could not resolve taxon id for query: {query}")


def fetch_all_observations(taxon_ids: list[int]) -> list[dict]:
    """Fetch all observations for place + taxa filters using cursor pagination."""
    observations: list[dict] = []
    batch = 1
    last_observation_id: int | None = None
    total_results = None

    while True:
        params = {
            "taxon_id": ",".join(str(t) for t in taxon_ids),
            "quality_grade": "research,needs_id,casual",
            "order_by": "id",
            "order": "asc",
            "per_page": PER_PAGE,
            **SHELBY_BOUNDS,
        }

        if last_observation_id is not None:
            params["id_above"] = last_observation_id

        data = get_json("/observations", params)
        page_results = data.get("results", [])

        if total_results is None:
            total_results = data.get("total_results")
            print(f"Total matching observations reported by API: {total_results}")

        if not page_results:
            break

        observations.extend(page_results)
        print(f"Fetched batch {batch}: {len(page_results)} rows (running total {len(observations)})")

        ids = [obs.get("id") for obs in page_results if isinstance(obs.get("id"), int)]
        if not ids:
            print("No numeric observation IDs in batch; stopping to avoid duplicate fetches.")
            break
        last_observation_id = max(ids)

        if len(page_results) < PER_PAGE:
            break

        batch += 1
        # Small pause to avoid hammering the API.
        time.sleep(0.2)

    return observations


def main() -> None:
    try:
        print("Resolving target taxa...")
        fungi_id = resolve_taxon_id("Fungi")
        slime_molds_id = resolve_taxon_id("slime molds", preferred_name_contains="slime")
        lichens_id = resolve_taxon_id("lichens", preferred_name_contains="lichen")
        mosses_id = resolve_taxon_id("mosses", preferred_name_contains="moss")
        taxon_ids = sorted({fungi_id, slime_molds_id, lichens_id, mosses_id})
        print(f"Using taxon ids: {taxon_ids}")

        print("Fetching observations from iNaturalist...")
        observations = fetch_all_observations(taxon_ids=taxon_ids)

        payload = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "area": {
                "name": "Shelby County, Tennessee area",
                "bounding_box": SHELBY_BOUNDS,
            },
            "taxa": {
                "fungi_taxon_id": fungi_id,
                "slime_molds_taxon_id": slime_molds_id,
                "lichens_taxon_id": lichens_id,
                "mosses_taxon_id": mosses_id,
            },
            "observation_count": len(observations),
            "observations": observations,
        }

        with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2)

        print(f"Saved {len(observations)} observations to {OUTPUT_FILE}")

    except (
        urllib.error.URLError,
        RemoteDisconnected,
        ssl.SSLError,
        socket.timeout,
        TimeoutError,
        ConnectionError,
        EOFError,
    ) as error:
        print(f"Network error while calling iNaturalist API: {error}")
    except Exception as error:  # noqa: BLE001
        print(f"Error: {error}")


if __name__ == "__main__":
    main()