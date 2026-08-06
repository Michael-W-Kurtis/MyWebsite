"""
Single source of truth for the pixelsort CLI surface.

Everything here was read off satyarth/pixelsort itself (argparams.py, interval.py,
constants.py), not paraphrased from the README. Two facts that matter and are easy
to get wrong:

  1. The package installs NO `pixelsort` console script. The only CLI entry point is
     `python -m pixelsort`. Anything that shells out to a bare `pixelsort` command
     will fail.
  2. Interval functions ignore kwargs they don't use (each takes **kwargs). So
     passing -t to `random` is silently accepted but does nothing. That is exactly
     why the UI hides irrelevant controls instead of just disabling them.

This module is serialized to JSON and injected into the wrapper page, so the
browser builds its command preview from the same rules the server executes.
"""

from __future__ import annotations

# pixelsort/constants.py DEFAULTS
DEFAULTS = {
    "interval_function": "threshold",
    "sorting_function": "lightness",
    "lower_threshold": 0.25,
    "upper_threshold": 0.8,
    "clength": 50,
    "angle": 0.0,
    "randomness": 0.0,
}

# pixelsort/interval.py `choices` keys, in README order.
# `uses` lists the kwargs each function actually reads in its signature.
INTERVAL_FUNCTIONS = [
    {
        "value": "random",
        "label": "random",
        "blurb": "Random interval widths, linear distribution.",
        "uses": ["clength"],
    },
    {
        "value": "threshold",
        "label": "threshold",
        "blurb": "Sort only pixels whose lightness sits between the two thresholds.",
        "uses": ["lower_threshold", "upper_threshold"],
    },
    {
        "value": "edges",
        "label": "edges",
        "blurb": "Edge detection defines the interval borders.",
        "uses": ["lower_threshold"],
    },
    {
        "value": "waves",
        "label": "waves",
        "blurb": "Near-uniform bands. Width follows char. length.",
        "uses": ["clength"],
    },
    {
        "value": "file",
        "label": "file",
        "blurb": "Intervals read from a black-and-white image you supply.",
        "uses": ["interval_file"],
    },
    {
        "value": "file-edges",
        "label": "file-edges",
        "blurb": "Edge detection run on the interval image you supply.",
        "uses": ["interval_file", "lower_threshold"],
    },
    {
        "value": "none",
        "label": "none",
        "blurb": "Sort entire rows, stopping only at the image border.",
        "uses": [],
    },
]

# pixelsort/sorting.py `choices` keys.
SORTING_FUNCTIONS = [
    {"value": "lightness", "label": "lightness", "blurb": "HSL lightness."},
    {"value": "hue", "label": "hue", "blurb": "HSL hue. Rainbow smears."},
    {"value": "saturation", "label": "saturation", "blurb": "HSL saturation."},
    {"value": "intensity", "label": "intensity", "blurb": "R + G + B."},
    {"value": "minimum", "label": "minimum", "blurb": "Lowest of R, G, B."},
]

# Controls that are always meaningful, whatever the interval function is.
ALWAYS_RELEVANT = ["angle", "randomness", "sorting_function"]

# UI metadata for every tunable. `flag` is the real argparse short flag.
CONTROLS = {
    "angle": {
        "flag": "-a",
        "label": "Angle",
        "hint": "Rotate before sorting. 90 gives vertical streaks.",
        "type": "range",
        "min": 0,
        "max": 360,
        "step": 1,
        "unit": "\u00b0",
    },
    "randomness": {
        "flag": "-r",
        "label": "Randomness",
        "hint": "Percentage of intervals left unsorted.",
        "type": "range",
        "min": 0,
        "max": 100,
        "step": 1,
        "unit": "%",
    },
    "lower_threshold": {
        "flag": "-t",
        "label": "Threshold (lower)",
        "hint": "Pixels darker than this are treated as borders.",
        "type": "range",
        "min": 0,
        "max": 1,
        "step": 0.01,
        "unit": "",
    },
    "upper_threshold": {
        "flag": "-u",
        "label": "Threshold (upper)",
        "hint": "Pixels brighter than this are treated as borders.",
        "type": "range",
        "min": 0,
        "max": 1,
        "step": 0.01,
        "unit": "",
    },
    "clength": {
        "flag": "-c",
        "label": "Char. length",
        "hint": "Characteristic interval width in pixels.",
        "type": "range",
        "min": 1,
        "max": 400,
        "step": 1,
        "unit": "px",
    },
    "interval_file": {
        "flag": "-f",
        "label": "Interval image",
        "hint": "Black-and-white image, same size as the input.",
        "type": "file",
    },
}

INTERVAL_VALUES = [f["value"] for f in INTERVAL_FUNCTIONS]
SORTING_VALUES = [f["value"] for f in SORTING_FUNCTIONS]
_USES = {f["value"]: f["uses"] for f in INTERVAL_FUNCTIONS}


def relevant_controls(interval_function: str) -> list[str]:
    """Which controls actually change the output for this interval function."""
    return ALWAYS_RELEVANT + _USES.get(interval_function, [])


class ParamError(ValueError):
    """Raised when a submitted parameter is outside the range the CLI accepts."""


def normalize(raw: dict) -> dict:
    """Coerce and clamp untrusted form input into a safe parameter dict.

    Irrelevant parameters are dropped rather than passed through, so the command
    shown to the user is the minimal command that reproduces the result.
    """
    interval = str(raw.get("interval_function", DEFAULTS["interval_function"]))
    if interval not in INTERVAL_VALUES:
        raise ParamError(f"Unknown interval function: {interval!r}")

    sorting = str(raw.get("sorting_function", DEFAULTS["sorting_function"]))
    if sorting not in SORTING_VALUES:
        raise ParamError(f"Unknown sorting function: {sorting!r}")

    def num(key, cast, lo, hi):
        try:
            value = cast(raw.get(key, DEFAULTS[key]))
        except (TypeError, ValueError):
            raise ParamError(f"{key} must be a number") from None
        return max(lo, min(hi, value))

    params = {
        "interval_function": interval,
        "sorting_function": sorting,
        "angle": num("angle", float, 0.0, 360.0),
        # The CLI takes randomness as a percentage (0-100), matching the README.
        "randomness": num("randomness", float, 0.0, 100.0),
    }

    keep = set(relevant_controls(interval))
    if "lower_threshold" in keep:
        params["lower_threshold"] = num("lower_threshold", float, 0.0, 1.0)
    if "upper_threshold" in keep:
        params["upper_threshold"] = num("upper_threshold", float, 0.0, 1.0)
    if "clength" in keep:
        params["clength"] = num("clength", int, 1, 400)

    if "lower_threshold" in params and "upper_threshold" in params:
        if params["lower_threshold"] > params["upper_threshold"]:
            raise ParamError("Lower threshold cannot exceed the upper threshold.")

    return params


def _fmt(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def build_argv(params: dict, input_path: str, output_path: str,
               interval_file: str | None = None) -> list[str]:
    """Build the argv list that is actually executed.

    Flags whose value equals the pixelsort default are omitted, which keeps the
    displayed command short and honest about what is being overridden.
    """
    argv = ["python", "-m", "pixelsort", input_path, "-o", output_path]

    if params["interval_function"] != DEFAULTS["interval_function"]:
        argv += ["-i", params["interval_function"]]
    if params["sorting_function"] != DEFAULTS["sorting_function"]:
        argv += ["-s", params["sorting_function"]]

    for key, flag in (("lower_threshold", "-t"), ("upper_threshold", "-u"),
                      ("clength", "-c"), ("angle", "-a"), ("randomness", "-r")):
        if key in params and params[key] != DEFAULTS[key]:
            argv += [flag, _fmt(params[key])]

    if interval_file and params["interval_function"] in ("file", "file-edges"):
        argv += ["-f", interval_file]

    return argv


def spec_for_client() -> dict:
    """The exact blob handed to the browser so its preview logic can't drift."""
    return {
        "defaults": DEFAULTS,
        "intervalFunctions": INTERVAL_FUNCTIONS,
        "sortingFunctions": SORTING_FUNCTIONS,
        "alwaysRelevant": ALWAYS_RELEVANT,
        "controls": CONTROLS,
    }
