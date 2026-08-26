## @file fleet_reference.py
#  @brief Rolling stock reference: maps a vehicle number to its rolling
#  stock type, expected door count and physical door numbering scheme,
#  based on ranges loaded from data/rolling_stock_ranges.json.

import json
from pathlib import Path

## Path to the JSON file listing vehicle number ranges and their rolling
#  stock type / door configuration.
REFERENCE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "rolling_stock_ranges.json"

## In-memory cache of the parsed reference file, populated on first use.
_ranges_cache = None

## Mapping from internal door index (1-16, matching the PX_IN/PX_OUT
#  columns in door_counts) to the physical door number, per rolling stock
#  family. Buses use their door index directly; tramway families differ in
#  how many doors they have and how the higher door indices map to
#  physical door numbers.
DOOR_SCHEMES = {
    "bus": {1: 1, 2: 2, 3: 3},
    "tram_302": {
        1: 11, 2: 12, 3: 13, 4: 14, 5: 15, 6: 16,
        7: 26, 8: 25, 9: 24, 10: 23, 11: 22, 12: 21,
    },
    "tram_401_402_urbos": {
        1: 11, 2: 12, 3: 13, 4: 14, 5: 15, 6: 16,
        7: 31, 8: 32, 9: 33, 10: 34, 11: 26, 12: 25, 13: 24, 14: 23, 15: 22, 16: 21,
    },
}


def get_physical_door_number(scheme_name, door_index):
    """!
    @brief Translate an internal door index into its physical door number.

    @param scheme_name Key into DOOR_SCHEMES, or None/unknown to disable
    translation.
    @param door_index Internal door index (1-16).
    @return Physical door number, or door_index unchanged if scheme_name is
    not found or has no mapping for that index.
    """
    scheme = DOOR_SCHEMES.get(scheme_name)
    if not scheme:
        return door_index
    return scheme.get(door_index, door_index)


def _load_ranges():
    """!
    @brief Load and parse the rolling stock reference JSON file.

    @return List of range entries (dicts), or an empty list if the file
    does not exist.
    """
    if not REFERENCE_FILE.exists():
        return []
    with open(REFERENCE_FILE, encoding="utf-8") as f:
        return json.load(f)


def get_rolling_stock(num_parc):
    """!
    @brief Look up the rolling stock configuration for a given vehicle
    number.

    @param num_parc Vehicle number (any type convertible to int).
    @return Dict with keys type (str), door_count (int or None),
    door_scheme (str or None), minimum_doors (int, defaults to 0); or None
    if num_parc is not convertible to int or falls outside every
    configured range.
    """
    global _ranges_cache
    if _ranges_cache is None:
        _ranges_cache = _load_ranges()

    try:
        num_parc = int(num_parc)
    except (ValueError, TypeError):
        return None

    for entry in _ranges_cache:
        if entry["range_start"] <= num_parc <= entry["range_end"]:
            return {
                "type": entry["type"],
                "door_count": entry.get("door_count"),
                "door_scheme": entry.get("door_scheme"),
                "minimum_doors": entry.get("minimum_doors", 0),
            }
    return None


def is_known_vehicle(num_parc) -> bool:
    """!
    @brief Check whether a vehicle number falls within a configured rolling
    stock range.

    @param num_parc Vehicle number to check.
    @return True if a matching range is found, False otherwise.
    """
    return get_rolling_stock(num_parc) is not None
