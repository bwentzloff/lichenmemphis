"""Generate an HTML report of stronger ecological metrics.

Reads a local iNaturalist observations JSON export and creates a report with:
1. Lichen species richness by year (unique species count)
2. Sensitive-species observation counts by year
"""

from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_INPUT_FILE = "shelby_county_tn_inat_observations.json"
DEFAULT_OUTPUT_FILE = "docs/index.html"
GRAPH_START_YEAR = 2018

SENSITIVE_TAXA = [
    "Usnea",
    "Ramalina",
    "Evernia",
    "Peltigera",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate an HTML page with lichen species richness and "
            "sensitive-species observation counts by year."
        )
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT_FILE,
        help=f"Input JSON file (default: {DEFAULT_INPUT_FILE})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_FILE,
        help=f"Output HTML file (default: {DEFAULT_OUTPUT_FILE})",
    )
    return parser.parse_args()


def extract_year(observation: dict) -> int | None:
    details = observation.get("observed_on_details") or {}
    year = details.get("year")
    if isinstance(year, int):
        return year

    observed_on = observation.get("observed_on")
    if isinstance(observed_on, str) and len(observed_on) >= 4:
        try:
            return int(observed_on[:4])
        except ValueError:
            return None

    return None


def extract_species_name(observation: dict) -> str:
    taxon = observation.get("taxon") or {}
    scientific_name = taxon.get("name")
    if isinstance(scientific_name, str) and scientific_name.strip():
        return scientific_name.strip()

    species_guess = observation.get("species_guess")
    if isinstance(species_guess, str) and species_guess.strip():
        return species_guess.strip()

    return "Unknown species"


def classify_group(observation: dict, taxa: dict) -> str | None:
    taxon = observation.get("taxon") or {}
    ancestor_ids = set(taxon.get("ancestor_ids") or [])
    taxon_id = taxon.get("id")
    if isinstance(taxon_id, int):
        ancestor_ids.add(taxon_id)

    lichen_id = taxa.get("lichens_taxon_id")
    slime_molds_id = taxa.get("slime_molds_taxon_id")
    mosses_id = taxa.get("mosses_taxon_id")
    fungi_id = taxa.get("fungi_taxon_id")

    # Check narrower groups first so lichen observations are not double-counted as fungi.
    if isinstance(lichen_id, int) and lichen_id in ancestor_ids:
        return "lichen"
    if isinstance(slime_molds_id, int) and slime_molds_id in ancestor_ids:
        return "slime_molds"
    if isinstance(mosses_id, int) and mosses_id in ancestor_ids:
        return "mosses"
    if isinstance(fungi_id, int) and fungi_id in ancestor_ids:
        return "fungi"

    return None


def is_sensitive_species(species_name: str) -> bool:
    first_token = species_name.split(" ", 1)[0].strip()
    if not first_token:
        return False
    return first_token in SENSITIVE_TAXA


def extract_coordinates(observation: dict) -> tuple[float, float] | None:
    geojson = observation.get("geojson") or {}
    coordinates = geojson.get("coordinates")
    if (
        isinstance(coordinates, list)
        and len(coordinates) >= 2
        and isinstance(coordinates[0], (int, float))
        and isinstance(coordinates[1], (int, float))
    ):
        # GeoJSON order is [longitude, latitude].
        return float(coordinates[1]), float(coordinates[0])

    location = observation.get("location")
    if isinstance(location, str) and "," in location:
        lat_str, lng_str = location.split(",", 1)
        try:
            return float(lat_str), float(lng_str)
        except ValueError:
            return None

    return None


def build_stats(
    data: dict,
) -> tuple[
    dict[int, int],
    dict[int, int],
    dict[int, set[str]],
    list[dict],
    tuple[int, int],
    dict[str, dict[int, int]],
]:
    observations = data.get("observations") or []
    taxa = data.get("taxa") or {}

    lichen_species_by_year: dict[int, set[str]] = defaultdict(set)
    sensitive_obs_by_year: dict[int, int] = defaultdict(int)
    sensitive_species_by_year: dict[int, set[str]] = defaultdict(set)
    group_observations_by_year: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    map_points: list[dict] = []
    min_year: int | None = None
    max_year: int | None = None

    for observation in observations:
        if not isinstance(observation, dict):
            continue

        year = extract_year(observation)
        if year is None:
            continue

        if min_year is None or year < min_year:
            min_year = year
        if max_year is None or year > max_year:
            max_year = year

        group = classify_group(observation, taxa) or "unknown"
        species_name = extract_species_name(observation)

        if group in {"fungi", "lichen", "slime_molds", "mosses"}:
            group_observations_by_year[group][year] += 1

        coordinates = extract_coordinates(observation)
        if coordinates is not None:
            lat, lng = coordinates
            map_points.append(
                {
                    "id": observation.get("id"),
                    "year": year,
                    "lat": lat,
                    "lng": lng,
                    "species": species_name,
                    "group": group,
                    "observed_on": observation.get("observed_on") or "unknown date",
                    "uri": observation.get("uri") or "",
                }
            )

        if group != "lichen":
            continue

        if species_name == "Unknown species":
            continue

        lichen_species_by_year[year].add(species_name)

        if is_sensitive_species(species_name):
            sensitive_obs_by_year[year] += 1
            sensitive_species_by_year[year].add(species_name)

    richness_by_year = {year: len(species_set) for year, species_set in lichen_species_by_year.items()}

    if min_year is None or max_year is None:
        min_year, max_year = 0, 0

    return (
        richness_by_year,
        dict(sensitive_obs_by_year),
        dict(sensitive_species_by_year),
        map_points,
        (min_year, max_year),
        {group: dict(year_counts) for group, year_counts in group_observations_by_year.items()},
    )


def render_species_richness_table(richness_by_year: dict[int, int]) -> str:
    parts: list[str] = []
    parts.append("<section>")
    parts.append("  <h2>Metric 1: Lichen Species Richness</h2>")
    parts.append("  <p>Unique lichen species observed in each year.</p>")

    if not richness_by_year:
        parts.append("  <p>No lichen observations found.</p>")
        parts.append("</section>")
        return "\n".join(parts)

    parts.append("  <table>")
    parts.append("    <caption>Lichen Species Richness by Year</caption>")
    parts.append("    <thead>")
    parts.append("      <tr>")
    parts.append("        <th scope=\"col\">Year</th>")
    parts.append("        <th scope=\"col\">Unique Lichen Species</th>")
    parts.append("      </tr>")
    parts.append("    </thead>")
    parts.append("    <tbody>")

    for year in sorted(richness_by_year):
        parts.append("      <tr>")
        parts.append(f"        <td>{year}</td>")
        parts.append(f"        <td>{richness_by_year[year]}</td>")
        parts.append("      </tr>")

    parts.append("    </tbody>")
    parts.append("  </table>")
    parts.append("</section>")
    return "\n".join(parts)


def render_sensitive_species_table(
    sensitive_obs_by_year: dict[int, int],
    sensitive_species_by_year: dict[int, set[str]],
) -> str:
    parts: list[str] = []
    parts.append("<section>")
    parts.append("  <h2>Metric 2: Sensitive-Species Observations</h2>")
    parts.append(
        "  <p>Number of lichen observations matching sensitive taxa by year "
        "(using genus-level matching).</p>"
    )
    parts.append("  <p>Sensitive taxa list:</p>")
    parts.append("  <ul>")
    for taxon in SENSITIVE_TAXA:
        parts.append(f"    <li>{html.escape(taxon)}</li>")
    parts.append("  </ul>")

    if not sensitive_obs_by_year:
        parts.append("  <p>No sensitive-species observations were found.</p>")
        parts.append("</section>")
        return "\n".join(parts)

    parts.append("  <table>")
    parts.append("    <caption>Sensitive-Species Observation Count by Year</caption>")
    parts.append("    <thead>")
    parts.append("      <tr>")
    parts.append("        <th scope=\"col\">Year</th>")
    parts.append("        <th scope=\"col\">Sensitive Observations</th>")
    parts.append("        <th scope=\"col\">Sensitive Species Observed</th>")
    parts.append("      </tr>")
    parts.append("    </thead>")
    parts.append("    <tbody>")

    for year in sorted(sensitive_obs_by_year):
        species_list = sorted(sensitive_species_by_year.get(year, set()), key=str.lower)
        species_text = ", ".join(species_list) if species_list else "-"
        parts.append("      <tr>")
        parts.append(f"        <td>{year}</td>")
        parts.append(f"        <td>{sensitive_obs_by_year[year]}</td>")
        parts.append(f"        <td>{html.escape(species_text)}</td>")
        parts.append("      </tr>")

    parts.append("    </tbody>")
    parts.append("  </table>")
    parts.append("</section>")
    return "\n".join(parts)


def render_map_section(min_year: int, max_year: int) -> str:
    parts: list[str] = []
    parts.append("<section>")
    parts.append("  <h2>Observation Map</h2>")
    parts.append("  <p>All observations are shown as pins.</p>")
    parts.append("  <div id=\"observation-map\" aria-label=\"Observation map\"></div>")
    parts.append("</section>")
    return "\n".join(parts)


def render_graph_filters_section(min_year: int, max_year: int) -> str:
    parts: list[str] = []
    parts.append('<details class="graph-filters">')
    parts.append("  <summary>Map Filters ⌄</summary>")
    parts.append("  <div class=\"graph-filter-stack\">")
    parts.append("    <p>Use these filters to control the map below.</p>")
    parts.append("    <div class=\"year-range-row\">")
    parts.append("      <div class=\"year-range-label\">From year - to year</div>")
    parts.append("      <div class=\"year-range-inputs\">")
    parts.append("        <label for=\"year-min\">From</label>")
    parts.append(
        f"        <input id=\"year-min\" type=\"number\" value=\"{max(min_year, GRAPH_START_YEAR)}\" min=\"{min_year}\" max=\"{max_year}\">"
    )
    parts.append("        <label for=\"year-max\">To</label>")
    parts.append(
        f"        <input id=\"year-max\" type=\"number\" value=\"{max_year}\" min=\"{min_year}\" max=\"{max_year}\">"
    )
    parts.append("      </div>")
    parts.append("    </div>")
    parts.append("    <p id=\"map-status\" class=\"map-status\">Showing all mapped observations</p>")
    parts.append("    <label class=\"ranges-toggle\"><input id=\"show-ranges\" type=\"checkbox\" checked> Show species ranges</label>")
    parts.append("    <div class=\"filters-row\">")
    parts.append("      <fieldset class=\"group-filters\">")
    parts.append("        <legend>Groups</legend>")
    parts.append("        <div class=\"group-filter-buttons\">")
    parts.append("          <button type=\"button\" id=\"groups-all\">All</button>")
    parts.append("          <button type=\"button\" id=\"groups-none\">None</button>")
    parts.append("        </div>")
    parts.append("        <div class=\"group-options\">")
    parts.append("          <label class=\"group-option\"><input type=\"checkbox\" name=\"group-filter\" value=\"fungi\" checked> Fungi</label>")
    parts.append("          <label class=\"group-option\"><input type=\"checkbox\" name=\"group-filter\" value=\"lichen\" checked> Lichen</label>")
    parts.append("          <label class=\"group-option\"><input type=\"checkbox\" name=\"group-filter\" value=\"slime_molds\" checked> Slime Molds</label>")
    parts.append("          <label class=\"group-option\"><input type=\"checkbox\" name=\"group-filter\" value=\"mosses\" checked> Mosses</label>")
    parts.append("        </div>")
    parts.append("      </fieldset>")
    parts.append("      <fieldset class=\"species-filter\">")
    parts.append("        <legend>Species (multi-select)</legend>")
    parts.append("        <div class=\"species-filter-buttons\">")
    parts.append("          <button type=\"button\" id=\"species-all\">All</button>")
    parts.append("          <button type=\"button\" id=\"species-none\">None</button>")
    parts.append("        </div>")
    parts.append(
        "        <select id=\"species-filter\" multiple size=\"8\" "
        "aria-label=\"Species filter\"></select>"
    )
    parts.append("        <p class=\"species-help\">Hold Command to select multiple species.</p>")
    parts.append("      </fieldset>")
    parts.append("    </div>")
    parts.append("  </div>")
    parts.append("</details>")
    return "\n".join(parts)


def render_info_box() -> str:
    info_html = (
        "<p>Lichens are living environmental sensors. Different species have different tolerances to pollution and environmental stress, which is why scientists have used them as bioindicators for decades. By documenting which lichen species are found around Memphis and tracking those observations over time, we can begin to build a community-driven picture of environmental change.</p><p>Mosses, fungi, and slime molds can also serve as useful bioindicators, but lichens are among the best-studied and easiest to observe.</p><p>This site automatically analyzes observations from iNaturalist. Every time someone uploads a lichen, fungus, slime mold, or moss observation, they're helping build a richer dataset for understanding local biodiversity and environmental health. New observations typically appear here within a day or two.</p>"
    )
    return "\n".join(
        [
            "<section class=\"info-box\">",
            "  <h2>About This Data</h2>",
            f"  {info_html}",
            "</section>",
        ]
    )


def render_observation_count_graph(
    group_observations_by_year: dict[str, dict[int, int]],
    year_bounds: tuple[int, int],
) -> str:
    group_meta = [
        ("fungi", "Fungi", "#1d4ed8"),
        ("lichen", "Lichens", "#0f766e"),
        ("slime_molds", "Slime Molds", "#b45309"),
        ("mosses", "Mosses", "#5b21b6"),
    ]

    min_year, max_year = year_bounds
    min_year = max(min_year, GRAPH_START_YEAR)
    if min_year > max_year:
        years: list[int] = []
    else:
        years = list(range(min_year, max_year + 1))

    max_count = 0
    for group_key, _, _ in group_meta:
        year_counts = group_observations_by_year.get(group_key, {})
        for year in years:
            max_count = max(max_count, year_counts.get(year, 0))
    max_count = max(max_count, 1)

    width = 940
    height = 320
    left = 56
    right = 20
    top = 20
    bottom = 48
    plot_width = width - left - right
    plot_height = height - top - bottom

    def x_for_index(idx: int) -> float:
        if len(years) <= 1:
            return left + plot_width / 2
        return left + (idx / (len(years) - 1)) * plot_width

    def y_for_count(count: int) -> float:
        return top + (1 - (count / max_count)) * plot_height

    parts: list[str] = []
    parts.append("<section>")
    parts.append("  <h2>Observations per Year (All Groups)</h2>")
    parts.append("  <p>One graph showing yearly observation counts for fungi, lichens, slime molds, and mosses.</p>")

    if not years:
        parts.append("  <p>No year data available for graphing.</p>")
        parts.append("</section>")
        return "\n".join(parts)

    parts.append('  <div class="obs-graph-wrap">')
    parts.append('    <div id="obs-graph-tooltip" class="obs-graph-tooltip" hidden></div>')
    parts.append(
        f'    <svg class="obs-graph" viewBox="0 0 {width} {height}" role="img" aria-label="Observation counts by year and group">'
    )

    # Horizontal grid lines and y-axis labels.
    for tick in range(6):
        value = round((max_count / 5) * tick)
        y = y_for_count(value)
        parts.append(
            f'      <line x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}" class="obs-grid" />'
        )
        parts.append(
            f'      <text x="{left - 8}" y="{y + 4:.2f}" text-anchor="end" class="obs-axis-label">{value}</text>'
        )

    # X-axis ticks (about 8 labels max).
    label_step = max(1, len(years) // 8)
    for idx, year in enumerate(years):
        if idx % label_step != 0 and idx != len(years) - 1:
            continue
        x = x_for_index(idx)
        parts.append(
            f'      <line x1="{x:.2f}" y1="{top + plot_height}" x2="{x:.2f}" y2="{top + plot_height + 5}" class="obs-axis" />'
        )
        parts.append(
            f'      <text x="{x:.2f}" y="{top + plot_height + 20}" text-anchor="middle" class="obs-axis-label">{year}</text>'
        )

    # Main axes.
    parts.append(
        f'      <line x1="{left}" y1="{top + plot_height}" x2="{width - right}" y2="{top + plot_height}" class="obs-axis" />'
    )
    parts.append(
        f'      <line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" class="obs-axis" />'
    )

    for group_key, label, color in group_meta:
        year_counts = group_observations_by_year.get(group_key, {})
        points = []
        for idx, year in enumerate(years):
            x = x_for_index(idx)
            y = y_for_count(year_counts.get(year, 0))
            points.append(f"{x:.2f},{y:.2f}")

        parts.append(
            f'      <polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2.3" />'
        )
        for idx, year in enumerate(years):
            count = year_counts.get(year, 0)
            x = x_for_index(idx)
            y = y_for_count(count)
            tooltip = f"{label} | {year}: {count} observations"
            parts.append(
                f'      <circle class="obs-hit-point" cx="{x:.2f}" cy="{y:.2f}" r="8" fill="transparent" '
                f'data-tooltip="{html.escape(tooltip)}"></circle>'
            )
            if count > 0:
                parts.append(f'      <circle cx="{x:.2f}" cy="{y:.2f}" r="2.7" fill="{color}" />')

    parts.append("    </svg>")
    parts.append("  </div>")
    parts.append('  <ul class="obs-legend">')
    for _, label, color in group_meta:
        parts.append(
            f'    <li><span class="obs-legend-swatch" style="background:{color}"></span>{html.escape(label)}</li>'
        )
    parts.append("  </ul>")
    parts.append("</section>")
    return "\n".join(parts)

def build_html(
    data: dict,
    richness_by_year: dict[int, int],
    sensitive_obs_by_year: dict[int, int],
    sensitive_species_by_year: dict[int, set[str]],
    map_points: list[dict],
    year_bounds: tuple[int, int],
    group_observations_by_year: dict[str, dict[int, int]],
) -> str:
    area_name = ((data.get("area") or {}).get("name") or "Selected Area")
    generated_at = data.get("generated_at_utc") or "Unknown"
    created_at = datetime.now(timezone.utc).isoformat()

    min_year, max_year = year_bounds
    sections = [
        render_info_box(),
        render_graph_filters_section(min_year, max_year),
        render_map_section(min_year, max_year),
        
        render_observation_count_graph(group_observations_by_year, year_bounds),
        render_species_richness_table(richness_by_year),
        render_sensitive_species_table(sensitive_obs_by_year, sensitive_species_by_year),
    ]
    map_points_json = json.dumps(map_points)

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            "  <title>Lichen Metrics by Year</title>",
            '  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin="">',
            "  <style>",
            "    :root {",
            "      color-scheme: light;",
            "      --bg: #f8fafc;",
            "      --panel: #ffffff;",
            "      --text: #102a43;",
            "      --muted: #486581;",
            "      --border: #d9e2ec;",
            "      --header: #243b53;",
            "      --stripe: #f0f4f8;",
            "      --accent: #006d77;",
            "    }",
            "    * { box-sizing: border-box; }",
            "    body {",
            "      margin: 0;",
            "      font-family: Georgia, \"Times New Roman\", serif;",
            "      color: var(--text);",
            "      background: linear-gradient(180deg, #eef7fb 0%, var(--bg) 180px);",
            "      line-height: 1.45;",
            "    }",
            "    header, main {",
            "      max-width: 1200px;",
            "      margin: 0 auto;",
            "      padding: 1rem 1.25rem;",
            "    }",
            "    h1 {",
            "      margin-bottom: 0.4rem;",
            "      color: var(--header);",
            "    }",
            "    h2 {",
            "      margin: 0 0 0.65rem;",
            "      color: var(--accent);",
            "    }",
            "    header p {",
            "      margin: 0.2rem 0;",
            "      color: var(--muted);",
            "    }",
            "    section {",
            "      background: var(--panel);",
            "      border: 1px solid var(--border);",
            "      border-radius: 10px;",
            "      padding: 1rem;",
            "      margin: 1rem 0 1.2rem;",
            "      overflow-x: auto;",
            "      box-shadow: 0 6px 20px rgba(16, 42, 67, 0.06);",
            "    }",
            "    .info-box {",
            "      border-left: 6px solid var(--accent);",
            "      background: #f5fcfd;",
            "    }",
            "    .graph-filters {",
            "      background: var(--panel);",
            "      border: 1px solid var(--border);",
            "      border-left: 6px solid #1d4ed8;",
            "      border-radius: 10px;",
            "      padding: 1rem;",
            "      margin: 1rem 0 1.2rem;",
            "      overflow-x: auto;",
            "      box-shadow: 0 6px 20px rgba(16, 42, 67, 0.06);",
            "    }",
            "    .graph-filters[open] {",
            "      background: var(--panel);",
            "    }",
            "    .graph-filters > summary {",
            "      cursor: pointer;",
            "      font-size: 1.5rem;",
            "      font-weight: 700;",
            "      color: var(--accent);",
            "      margin: 0 0 0.65rem;",
            "      padding: 0;",
            "      list-style: none;",
            "    }",
            "    .graph-filters > summary::-webkit-details-marker {",
            "      display: none;",
            "    }",
            "    .graph-filter-stack {",
            "      display: flex;",
            "      flex-direction: column;",
            "      gap: 0.85rem;",
            "    }",
            "    .info-box p {",
            "      margin: 0.35rem 0 0;",
            "    }",
            "    table {",
            "      width: 100%;",
            "      border-collapse: collapse;",
            "      font-size: 0.95rem;",
            "    }",
            "    .obs-graph-wrap {",
            "      position: relative;",
            "      width: 100%;",
            "      overflow-x: auto;",
            "      border: 1px solid var(--border);",
            "      border-radius: 8px;",
            "      background: #fbfeff;",
            "      margin-top: 0.4rem;",
            "    }",
            "    .obs-hit-point {",
            "      pointer-events: all;",
            "      cursor: crosshair;",
            "    }",
            "    .obs-graph-tooltip {",
            "      position: absolute;",
            "      z-index: 8;",
            "      pointer-events: none;",
            "      background: rgba(16, 42, 67, 0.94);",
            "      color: #f0f4f8;",
            "      border-radius: 6px;",
            "      padding: 0.35rem 0.5rem;",
            "      font-size: 0.82rem;",
            "      line-height: 1.25;",
            "      box-shadow: 0 6px 18px rgba(16, 42, 67, 0.26);",
            "      transform: translate(-50%, calc(-100% - 10px));",
            "      white-space: nowrap;",
            "    }",
            "    .obs-graph {",
            "      width: 100%;",
            "      min-width: 760px;",
            "      height: auto;",
            "      display: block;",
            "    }",
            "    .obs-grid {",
            "      stroke: #d9e2ec;",
            "      stroke-width: 1;",
            "    }",
            "    .obs-axis {",
            "      stroke: #486581;",
            "      stroke-width: 1.3;",
            "    }",
            "    .obs-axis-label {",
            "      fill: #334e68;",
            "      font-size: 11px;",
            "      font-family: Georgia, \"Times New Roman\", serif;",
            "    }",
            "    .obs-legend {",
            "      list-style: none;",
            "      padding: 0;",
            "      margin: 0.65rem 0 0;",
            "      display: flex;",
            "      gap: 0.9rem;",
            "      flex-wrap: wrap;",
            "    }",
            "    .obs-legend li {",
            "      display: inline-flex;",
            "      align-items: center;",
            "      gap: 0.45rem;",
            "      font-weight: 600;",
            "      color: var(--header);",
            "    }",
            "    .obs-legend-swatch {",
            "      width: 14px;",
            "      height: 4px;",
            "      border-radius: 3px;",
            "      display: inline-block;",
            "    }",
            "    #observation-map {",
            "      width: 100%;",
            "      height: 520px;",
            "      border: 1px solid var(--border);",
            "      border-radius: 8px;",
            "      overflow: hidden;",
            "      margin-top: 0.4rem;",
            "    }",
            "    .map-controls {",
            "      display: flex;",
            "      flex-direction: column;",
            "      gap: 0.65rem;",
            "      margin-bottom: 0.5rem;",
            "    }",
            "    .map-controls label {",
            "      font-weight: 600;",
            "      color: var(--header);",
            "    }",
            "    .map-controls input {",
            "      width: 100%;",
            "      padding: 0.36rem 0.45rem;",
            "      border: 1px solid var(--border);",
            "      border-radius: 6px;",
            "      background: #fff;",
            "      color: var(--text);",
            "      font: inherit;",
            "    }",
            "    .map-controls input:focus {",
            "      outline: 2px solid #9ad9df;",
            "      outline-offset: 1px;",
            "      border-color: var(--accent);",
            "    }",
            "    .map-status {",
            "      margin: 0.1rem 0 0.25rem;",
            "      color: var(--muted);",
            "      font-size: 0.92rem;",
            "    }",
            "    .year-range-row {",
            "      display: flex;",
            "      flex-direction: column;",
            "      gap: 0.35rem;",
            "    }",
            "    .year-range-label {",
            "      font-weight: 700;",
            "      color: var(--header);",
            "    }",
            "    .year-range-inputs {",
            "      display: flex;",
            "      flex-wrap: wrap;",
            "      align-items: center;",
            "      gap: 0.45rem 0.7rem;",
            "    }",
            "    .year-range-inputs label {",
            "      font-weight: 600;",
            "      color: var(--header);",
            "    }",
            "    .year-range-inputs input {",
            "      width: 110px;",
            "      padding: 0.36rem 0.45rem;",
            "      border: 1px solid var(--border);",
            "      border-radius: 6px;",
            "      background: #fff;",
            "      color: var(--text);",
            "      font: inherit;",
            "    }",
            "    .year-range-inputs input:focus {",
            "      outline: 2px solid #9ad9df;",
            "      outline-offset: 1px;",
            "      border-color: var(--accent);",
            "    }",
            "    .ranges-toggle {",
            "      display: inline-flex;",
            "      align-items: center;",
            "      gap: 0.45rem;",
            "      font-weight: 600;",
            "      color: var(--header);",
            "    }",
            "    .ranges-toggle input[type=checkbox] {",
            "      margin: 0;",
            "    }",
            "    .filters-row {",
            "      display: grid;",
            "      grid-template-columns: minmax(260px, 0.95fr) minmax(260px, 1.05fr);",
            "      gap: 1rem;",
            "      align-items: start;",
            "    }",
            "    .group-filters, .species-filter {",
            "      margin: 0;",
            "      padding: 0.75rem 0.85rem 0.85rem;",
            "      border: 1px solid var(--border);",
            "      border-radius: 10px;",
            "      background: #ffffff;",
            "      min-width: 0;",
            "    }",
            "    .group-filters legend, .species-filter legend {",
            "      padding: 0 0.3rem;",
            "      font-weight: 700;",
            "      color: var(--header);",
            "    }",
            "    .group-filter-buttons, .species-filter-buttons {",
            "      display: flex;",
            "      flex-wrap: wrap;",
            "      gap: 0.4rem;",
            "      margin: 0.25rem 0 0.6rem;",
            "    }",
            "    .group-filter-buttons button, .species-filter-buttons button {",
            "      border: 1px solid var(--border);",
            "      border-radius: 6px;",
            "      background: #fff;",
            "      color: var(--header);",
            "      padding: 0.25rem 0.55rem;",
            "      font: inherit;",
            "      cursor: pointer;",
            "    }",
            "    .group-filter-buttons button:hover, .species-filter-buttons button:hover {",
            "      border-color: var(--accent);",
            "    }",
            "    .group-options {",
            "      display: flex;",
            "      flex-direction: column;",
            "      align-items: flex-start;",
            "      gap: 0.4rem;",
            "    }",
            "    .group-option {",
            "      display: flex;",
            "      align-items: center;",
            "      gap: 0.45rem;",
            "      font-weight: 500;",
            "      color: var(--text);",
            "      width: fit-content;",
            "      justify-content: flex-start;",
            "      min-width: 0;",
            "      text-align: left;",
            "    }",
            "    .group-option input[type=checkbox] {",
            "      margin: 0;",
            "      flex: 0 0 auto;",
            "    }",
            "    .species-filter {",
            "      display: flex;",
            "      flex-direction: column;",
            "      gap: 0.4rem;",
            "    }",
            "    .species-filter label {",
            "      font-weight: 600;",
            "      color: var(--header);",
            "    }",
            "    #species-filter {",
            "      width: 100%;",
            "      border: 1px solid var(--border);",
            "      border-radius: 6px;",
            "      background: #fff;",
            "      color: var(--text);",
            "      font: inherit;",
            "      padding: 0.25rem;",
            "    }",
            "    #species-filter option.species-option-genus {",
            "      font-weight: 700;",
            "      color: #1f4f66;",
            "      background: #eef8fb;",
            "    }",
            "    .species-help {",
            "      margin: 0.35rem 0 0;",
            "      font-size: 0.84rem;",
            "      color: var(--muted);",
            "    }",
            "    caption {",
            "      text-align: left;",
            "      font-weight: 700;",
            "      margin-bottom: 0.6rem;",
            "      color: var(--header);",
            "    }",
            "    th, td {",
            "      border: 1px solid var(--border);",
            "      padding: 0.45rem 0.5rem;",
            "      text-align: left;",
            "      vertical-align: top;",
            "    }",
            "    th {",
            "      background: #dceef1;",
            "      color: #1f2933;",
            "      position: sticky;",
            "      top: 0;",
            "      z-index: 1;",
            "    }",
            "    tbody tr:nth-child(even) {",
            "      background: var(--stripe);",
            "    }",
            "    td:nth-child(1), td:nth-child(2) {",
            "      white-space: nowrap;",
            "    }",
            "    @media (max-width: 700px) {",
            "      body { font-size: 15px; }",
            "      section { padding: 0.8rem; }",
            "      #observation-map { height: 420px; }",
            "      .year-range-inputs { gap: 0.35rem; }",
            "      .year-range-inputs input { width: 100%; max-width: 160px; }",
            "      .filters-row { grid-template-columns: 1fr; }",
            "      th, td { padding: 0.4rem 0.45rem; }",
            "    }",
            "  </style>",
            "</head>",
            "<body>",
            "<header>",
            "  <h1>Lichen Environmental Metrics by Year</h1>",
            f"  <p>Area: {html.escape(str(area_name))}</p>",
            f"  <p>Source generated at (UTC): {html.escape(str(generated_at))}</p>",
            f"  <p>Report created at (UTC): {html.escape(created_at)}</p>",
            "</header>",
            "<main>",
            *sections,
            "</main>",
            '  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>',
            "  <script>",
            "    (function () {",
            f"      const observationPoints = {map_points_json};",
            f"      const minYearBound = {min_year};",
            f"      const maxYearBound = {max_year};",
            "",
            "      function clamp(value, minValue, maxValue) {",
            "        return Math.max(minValue, Math.min(maxValue, value));",
            "      }",
            "",
            "      function escapeHtml(text) {",
            "        return String(text)",
            "          .replaceAll('&', '&amp;')",
            "          .replaceAll('<', '&lt;')",
            "          .replaceAll('>', '&gt;')",
            "          .replaceAll('\"', '&quot;')",
            "          .replaceAll(\"'\", '&#39;');",
            "      }",
            "",
            "      const minInput = document.getElementById('year-min');",
            "      const maxInput = document.getElementById('year-max');",
            "      const mapStatus = document.getElementById('map-status');",
            "      const groupInputs = Array.from(document.querySelectorAll('input[name=\"group-filter\"]'));",
            "      const groupsAllBtn = document.getElementById('groups-all');",
            "      const groupsNoneBtn = document.getElementById('groups-none');",
            "      const showRangesInput = document.getElementById('show-ranges');",
            "      const speciesSelect = document.getElementById('species-filter');",
            "      const speciesAllBtn = document.getElementById('species-all');",
            "      const speciesNoneBtn = document.getElementById('species-none');",
            "      const mapElement = document.getElementById('observation-map');",
            "      if (",
            "        !minInput || !maxInput || !mapStatus || !mapElement || !window.L ||",
            "        !speciesSelect || groupInputs.length === 0",
            "      ) {",
            "        return;",
            "      }",
            "",
            "      const map = L.map('observation-map');",
            "      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {",
            "        maxZoom: 19,",
            "        attribution: '&copy; OpenStreetMap contributors'",
            "      }).addTo(map);",
            "",
            "      const markerLayer = L.layerGroup().addTo(map);",
            "      const rangeLayer = L.layerGroup().addTo(map);",
            "      const allLatLngs = observationPoints.map(function (point) {",
            "        return [point.lat, point.lng];",
            "      });",
            "",
            "      if (allLatLngs.length > 0) {",
            "        map.fitBounds(allLatLngs, { padding: [20, 20] });",
            "      } else {",
            "        map.setView([35.15, -89.95], 10);",
            "      }",
            "",
            "      function getSelectedGroups() {",
            "        return new Set(",
            "          groupInputs.filter(function (input) { return input.checked; }).map(function (input) { return input.value; })",
            "        );",
            "      }",
            "",
            "      function getSelectedSpecies() {",
            "        return new Set(",
            "          Array.from(speciesSelect.options)",
            "            .filter(function (option) { return option.selected; })",
            "            .map(function (option) { return option.value; })",
            "        );",
            "      }",
            "",
            "      function speciesMatchesSelection(pointSpecies, selectedSpecies) {",
            "        if (selectedSpecies.size === 0) {",
            "          return true;",
            "        }",
            "",
            "        const species = pointSpecies || 'Unknown species';",
            "        for (const selected of selectedSpecies) {",
            "          if (selected.startsWith('genus:')) {",
            "            const genus = selected.slice(6);",
            "            if (species === genus || species.startsWith(genus + ' ')) {",
            "              return true;",
            "            }",
            "            continue;",
            "          }",
            "          if (species === selected) {",
            "            return true;",
            "          }",
            "          if (species.startsWith(selected + ' ')) {",
            "            return true;",
            "          }",
            "        }",
            "        return false;",
            "      }",
            "",
            "      function pointMatchesFilters(point, minYear, maxYear, selectedGroups, selectedSpecies) {",
            "        if (point.year < minYear || point.year > maxYear) {",
            "          return false;",
            "        }",
            "        if (!selectedGroups.has(point.group)) {",
            "          return false;",
            "        }",
            "        if (!speciesMatchesSelection(point.species, selectedSpecies)) {",
            "          return false;",
            "        }",
            "        return true;",
            "      }",
            "",
            "      function pointsByYearAndGroup(minYear, maxYear, selectedGroups) {",
            "        return observationPoints.filter(function (point) {",
            "          return point.year >= minYear && point.year <= maxYear && selectedGroups.has(point.group);",
            "        });",
            "      }",
            "",
            "      function updateSpeciesOptions(minYear, maxYear, selectedGroups) {",
            "        const previousSelection = getSelectedSpecies();",
            "        const hadExistingOptions = speciesSelect.options.length > 0;",
            "        const candidates = new Set();",
            "        pointsByYearAndGroup(minYear, maxYear, selectedGroups).forEach(function (point) {",
            "          candidates.add(point.species || 'Unknown species');",
            "        });",
            "",
            "        const sortedSpecies = Array.from(candidates).sort(function (a, b) {",
            "          return a.localeCompare(b, undefined, { sensitivity: 'base' });",
            "        });",
            "",
            "        speciesSelect.innerHTML = '';",

            "        const genera = new Set();",
            "        sortedSpecies.forEach(function (species) {",
            "          const firstToken = String(species).split(' ', 1)[0].trim();",
            "          if (firstToken) {",
            "            genera.add(firstToken);",
            "          }",
            "        });",

            "        const sortedGenera = Array.from(genera).sort(function (a, b) {",
            "          return a.localeCompare(b, undefined, { sensitivity: 'base' });",
            "        });",

            "        sortedGenera.forEach(function (genus) {",
            "          const option = document.createElement('option');",
            "          option.value = 'genus:' + genus;",
            "          option.textContent = genus + ' (all species)';",
            "          option.selected = !hadExistingOptions || previousSelection.has(option.value);",
            "          option.className = 'species-option-genus';",
            "          speciesSelect.appendChild(option);",
            "        });",

            "        sortedSpecies.forEach(function (species) {",
            "          const option = document.createElement('option');",
            "          option.value = species;",
            "          option.textContent = species;",
            "          option.selected = !hadExistingOptions || previousSelection.has(species);",
            "          speciesSelect.appendChild(option);",
            "        });",
            "      }",
            "",
            "      function renderMarkers(minYear, maxYear, selectedGroups, selectedSpecies) {",
            "        markerLayer.clearLayers();",
            "        let shown = 0;",
            "",
            "        observationPoints.forEach(function (point) {",
            "          if (!pointMatchesFilters(point, minYear, maxYear, selectedGroups, selectedSpecies)) {",
            "            return;",
            "          }",
            "          shown += 1;",
            "",
            "          const marker = L.circleMarker([point.lat, point.lng], {",
            "            radius: 4,",
            "            color: '#0b7285',",
            "            weight: 1,",
            "            opacity: 0.85,",
            "            fillColor: '#1098ad',",
            "            fillOpacity: 0.65",
            "          });",
            "",
            "          const species = escapeHtml(point.species || 'Unknown species');",
            "          const observedOn = escapeHtml(point.observed_on || 'unknown date');",
            "          const groupLabelMap = { fungi: 'Fungi', lichen: 'Lichen', slime_molds: 'Slime Molds', mosses: 'Mosses' };",
            "          const groupLabel = escapeHtml(groupLabelMap[point.group] || point.group || 'Unknown');",
            "          const details = point.uri",
            "            ? '<br><a href=\"' + escapeHtml(point.uri) + '\" target=\"_blank\" rel=\"noopener noreferrer\">Open observation</a>'",
            "            : '';",
            "          marker.bindPopup('<strong>' + species + '</strong><br>Group: ' + groupLabel + '<br>Year: ' + point.year + '<br>Observed on: ' + observedOn + details);",
            "          marker.addTo(markerLayer);",
            "        });",
            "",
            "        mapStatus.textContent = 'Showing ' + shown + ' observations for ' + minYear + ' to ' + maxYear + '.';",
            "      }",
            "",
            "      function haversineKm(a, b) {",
            "        const rad = Math.PI / 180;",
            "        const dLat = (b.lat - a.lat) * rad;",
            "        const dLng = (b.lng - a.lng) * rad;",
            "        const lat1 = a.lat * rad;",
            "        const lat2 = b.lat * rad;",
            "        const s1 = Math.sin(dLat / 2);",
            "        const s2 = Math.sin(dLng / 2);",
            "        const c = 2 * Math.atan2(Math.sqrt(s1 * s1 + Math.cos(lat1) * Math.cos(lat2) * s2 * s2), Math.sqrt(1 - (s1 * s1 + Math.cos(lat1) * Math.cos(lat2) * s2 * s2)));",
            "        return 6371 * c;",
            "      }",
            "",
            "      function speciesColor(species) {",
            "        let hash = 0;",
            "        for (let i = 0; i < species.length; i += 1) {",
            "          hash = (hash * 31 + species.charCodeAt(i)) >>> 0;",
            "        }",
            "        const hue = hash % 360;",
            "        return 'hsl(' + hue + ' 70% 42%)';",
            "      }",
            "",
            "      function clusterPoints(points, epsilonKm) {",
            "        const visited = new Array(points.length).fill(false);",
            "        const clusters = [];",
            "",
            "        for (let i = 0; i < points.length; i += 1) {",
            "          if (visited[i]) {",
            "            continue;",
            "          }",
            "          visited[i] = true;",
            "          const clusterIndices = [i];",
            "          const queue = [i];",
            "",
            "          while (queue.length > 0) {",
            "            const current = queue.pop();",
            "            for (let j = 0; j < points.length; j += 1) {",
            "              if (visited[j]) {",
            "                continue;",
            "              }",
            "              if (haversineKm(points[current], points[j]) <= epsilonKm) {",
            "                visited[j] = true;",
            "                clusterIndices.push(j);",
            "                queue.push(j);",
            "              }",
            "            }",
            "          }",
            "",
            "          clusters.push(clusterIndices.map(function (idx) { return points[idx]; }));",
            "        }",
            "",
            "        return clusters;",
            "      }",
            "",
            "      function convexHullLatLng(points) {",
            "        if (points.length <= 2) {",
            "          return points.map(function (p) { return [p.lat, p.lng]; });",
            "        }",
            "",
            "        const sorted = points.slice().sort(function (a, b) {",
            "          if (a.lng !== b.lng) { return a.lng - b.lng; }",
            "          return a.lat - b.lat;",
            "        });",
            "",
            "        function cross(o, a, b) {",
            "          return (a.lng - o.lng) * (b.lat - o.lat) - (a.lat - o.lat) * (b.lng - o.lng);",
            "        }",
            "",
            "        const lower = [];",
            "        sorted.forEach(function (point) {",
            "          while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], point) <= 0) {",
            "            lower.pop();",
            "          }",
            "          lower.push(point);",
            "        });",
            "",
            "        const upper = [];",
            "        for (let i = sorted.length - 1; i >= 0; i -= 1) {",
            "          const point = sorted[i];",
            "          while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], point) <= 0) {",
            "            upper.pop();",
            "          }",
            "          upper.push(point);",
            "        }",
            "",
            "        lower.pop();",
            "        upper.pop();",
            "        return lower.concat(upper).map(function (p) { return [p.lat, p.lng]; });",
            "      }",
            "",
            "      function renderRanges(minYear, maxYear, selectedGroups, selectedSpecies) {",
            "        rangeLayer.clearLayers();",
            "        if (!showRangesInput || !showRangesInput.checked) {",
            "          return;",
            "        }",
            "",
            "        const speciesToPoints = new Map();",
            "        observationPoints.forEach(function (point) {",
            "          if (!pointMatchesFilters(point, minYear, maxYear, selectedGroups, selectedSpecies)) {",
            "            return;",
            "          }",
            "          const key = point.species || 'Unknown species';",
            "          if (!speciesToPoints.has(key)) {",
            "            speciesToPoints.set(key, []);",
            "          }",
            "          speciesToPoints.get(key).push(point);",
            "        });",
            "",
            "        speciesToPoints.forEach(function (points, species) {",
            "          const clusters = clusterPoints(points, 3.0);",
            "          const color = speciesColor(species);",
            "",
            "          clusters.forEach(function (cluster) {",
            "            if (cluster.length === 1) {",
            "              L.circle([cluster[0].lat, cluster[0].lng], {",
            "                radius: 350,",
            "                color: color,",
            "                weight: 1.2,",
            "                fillColor: color,",
            "                fillOpacity: 0.08",
            "              }).bindTooltip(species + ' range (single observation)').addTo(rangeLayer);",
            "              return;",
            "            }",
            "",
            "            if (cluster.length === 2) {",
            "              L.polyline(cluster.map(function (p) { return [p.lat, p.lng]; }), {",
            "                color: color,",
            "                weight: 2,",
            "                opacity: 0.65,",
            "                dashArray: '5 4'",
            "              }).bindTooltip(species + ' range cluster').addTo(rangeLayer);",
            "              return;",
            "            }",
            "",
            "            const hull = convexHullLatLng(cluster);",
            "            L.polygon(hull, {",
            "              color: color,",
            "              weight: 1.6,",
            "              opacity: 0.85,",
            "              fillColor: color,",
            "              fillOpacity: 0.14",
            "            }).bindTooltip(species + ' range cluster').addTo(rangeLayer);",
            "          });",
            "        });",
            "      }",
            "",
            "      function applyFilters() {",
            "        let minYear = Number.parseInt(minInput.value, 10);",
            "        let maxYear = Number.parseInt(maxInput.value, 10);",
            "",
            "        if (Number.isNaN(minYear)) { minYear = minYearBound; }",
            "        if (Number.isNaN(maxYear)) { maxYear = maxYearBound; }",
            "",
            "        minYear = clamp(minYear, minYearBound, maxYearBound);",
            "        maxYear = clamp(maxYear, minYearBound, maxYearBound);",
            "",
            "        if (minYear > maxYear) {",
            "          const tmp = minYear;",
            "          minYear = maxYear;",
            "          maxYear = tmp;",
            "        }",
            "",
            "        minInput.value = String(minYear);",
            "        maxInput.value = String(maxYear);",
            "",
            "        const selectedGroups = getSelectedGroups();",
            "        updateSpeciesOptions(minYear, maxYear, selectedGroups);",
            "        const selectedSpecies = getSelectedSpecies();",
            "        renderMarkers(minYear, maxYear, selectedGroups, selectedSpecies);",
            "        renderRanges(minYear, maxYear, selectedGroups, selectedSpecies);",
            "      }",
            "",
            "      minInput.addEventListener('input', applyFilters);",
            "      maxInput.addEventListener('input', applyFilters);",
            "      groupInputs.forEach(function (input) {",
            "        input.addEventListener('change', applyFilters);",
            "      });",
            "      speciesSelect.addEventListener('change', applyFilters);",
            "      if (showRangesInput) {",
            "        showRangesInput.addEventListener('change', applyFilters);",
            "      }",
            "",
            "      if (groupsAllBtn) {",
            "        groupsAllBtn.addEventListener('click', function () {",
            "          groupInputs.forEach(function (input) { input.checked = true; });",
            "          applyFilters();",
            "        });",
            "      }",
            "",
            "      if (groupsNoneBtn) {",
            "        groupsNoneBtn.addEventListener('click', function () {",
            "          groupInputs.forEach(function (input) { input.checked = false; });",
            "          applyFilters();",
            "        });",
            "      }",
            "",
            "      if (speciesAllBtn) {",
            "        speciesAllBtn.addEventListener('click', function () {",
            "          Array.from(speciesSelect.options).forEach(function (option) { option.selected = true; });",
            "          applyFilters();",
            "        });",
            "      }",
            "",
            "      if (speciesNoneBtn) {",
            "        speciesNoneBtn.addEventListener('click', function () {",
            "          Array.from(speciesSelect.options).forEach(function (option) { option.selected = false; });",
            "          applyFilters();",
            "        });",
            "      }",
            "",
            "      const graphTooltip = document.getElementById('obs-graph-tooltip');",
            "      const graphWrap = document.querySelector('.obs-graph-wrap');",
            "      const graphPoints = Array.from(document.querySelectorAll('.obs-hit-point'));",
            "      function showGraphTooltip(event, point) {",
            "        if (!graphTooltip || !graphWrap || !point) {",
            "          return;",
            "        }",
            "        const tooltipText = point.getAttribute('data-tooltip') || '';",
            "        graphTooltip.textContent = tooltipText;",
            "        graphTooltip.hidden = false;",
            "",
            "        const wrapRect = graphWrap.getBoundingClientRect();",
            "        const pointRect = point.getBoundingClientRect();",
            "        const x = pointRect.left - wrapRect.left + pointRect.width / 2 + graphWrap.scrollLeft;",
            "        const y = pointRect.top - wrapRect.top + graphWrap.scrollTop;",
            "        graphTooltip.style.left = x + 'px';",
            "        graphTooltip.style.top = y + 'px';",
            "      }",
            "",
            "      function hideGraphTooltip() {",
            "        if (!graphTooltip) {",
            "          return;",
            "        }",
            "        graphTooltip.hidden = true;",
            "      }",
            "",
            "      graphPoints.forEach(function (point) {",
            "        point.setAttribute('tabindex', '0');",
            "        point.addEventListener('mouseenter', function (event) { showGraphTooltip(event, point); });",
            "        point.addEventListener('mousemove', function (event) { showGraphTooltip(event, point); });",
            "        point.addEventListener('mouseleave', hideGraphTooltip);",
            "        point.addEventListener('focus', function (event) { showGraphTooltip(event, point); });",
            "        point.addEventListener('blur', hideGraphTooltip);",
            "      });",
            "",
            "      applyFilters();",
            "    })();",
            "  </script>",
            "</body>",
            "</html>",
        ]
    )


def main() -> None:
    args = parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    with input_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    (
        richness_by_year,
        sensitive_obs_by_year,
        sensitive_species_by_year,
        map_points,
        year_bounds,
        group_observations_by_year,
    ) = build_stats(data)
    html_output = build_html(
        data,
        richness_by_year,
        sensitive_obs_by_year,
        sensitive_species_by_year,
        map_points,
        year_bounds,
        group_observations_by_year,
    )

    with output_path.open("w", encoding="utf-8") as file:
        file.write(html_output)

    print(f"Wrote report to {output_path}")


if __name__ == "__main__":
    main()