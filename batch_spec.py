"""
Grid definitions for the batch pixelsorter.

ADMINISTRATORS: every number in this file is meant to be edited.

Two ways to change them:

  1. Edit this file directly and rebuild the web image.
  2. Drop a JSON file at  web/content/batch_ladders.json  and it is merged over
     these defaults at import time. That directory is bind-mounted into the
     container, so a restart picks it up with no rebuild. Only the keys you
     include are overridden. See LADDERS below for the shape; a minimal example:

         { "ladders": { "clength": { "4": [5, 25, 90, 400] } } }

The ladder steps are perceptually spaced, not numerically spaced. They come from
measuring mean absolute pixel change against the unsorted original:

    randomness   0 →  30 →  60 →  90   gives  2.70 → 2.03 → 0.94 → 0.23
    clength      8 →  30 → 100 → 300   gives  0.43 → 1.56 → 4.29 → 8.35
    edges  -t 0.05 → .25 → .50 → .90   gives  0.73 → 5.38 → 9.95 → 10.87
    threshold band ±.05 → ±.20 → ±.35 → ±.50  gives 0.57 → 5.84 → 10.42 → 12.51

Two consequences are baked into the tables below and are easy to undo by accident:

  * `randomness` is the percentage of intervals NOT sorted, so it runs DESCENDING
    in every intensity ladder. Sorting it ascending makes the grid read backwards.
  * `edges` gains almost nothing above 0.5, and the threshold band compresses in
    its final step, so those ladders stop short of saturation. Spacing them evenly
    across the full 0-1 range wastes two of four columns on near-duplicates.
"""

from __future__ import annotations

import json
import logging
import math

from . import config

log = logging.getLogger("site.batch_spec")

GRID_SIZES = (3, 4)

# --------------------------------------------------------------------------
# Intensity-mode ladders. Keyed by ladder name, then by grid size.
# Index 0 is the subtlest step; the last index is the most extreme.
# --------------------------------------------------------------------------
LADDERS: dict[str, dict[int, list]] = {
    "randomness":      {3: [85, 40, 0],         4: [85, 55, 25, 0]},
    "clength":         {3: [10, 60, 300],       4: [8, 30, 100, 300]},
    "threshold_lower": {3: [0.40, 0.22, 0.0],   4: [0.42, 0.32, 0.18, 0.0]},
    "threshold_upper": {3: [0.60, 0.78, 1.0],   4: [0.58, 0.68, 0.82, 1.0]},
    "edge_threshold":  {3: [0.05, 0.22, 0.55],  4: [0.05, 0.18, 0.35, 0.60]},
    "angle_quadrant":  {3: [0, 60, 120],        4: [0, 45, 90, 135]},
}

# --------------------------------------------------------------------------
# Which two axes Intensity mode uses for each interval function, chosen from
# the parameters that interval function actually reads (see pixelsort_spec).
#
# `monotonic: false` marks an axis that varies the image without intensifying
# it. `file` and `none` read neither clength nor a threshold, so there is no
# second intensity axis available and angle fills the slot. The UI labels that
# case honestly rather than implying a ramp.
# --------------------------------------------------------------------------
_RANDOMNESS_ROW = {"param": "randomness", "ladder": "randomness", "monotonic": True}

INTENSITY_AXES: dict[str, dict] = {
    "random": {
        "row": _RANDOMNESS_ROW,
        "col": {"param": "clength", "ladder": "clength", "monotonic": True},
    },
    "waves": {
        "row": _RANDOMNESS_ROW,
        "col": {"param": "clength", "ladder": "clength", "monotonic": True},
    },
    "threshold": {
        "row": {"param": "lower_threshold", "ladder": "threshold_lower", "monotonic": True},
        "col": {"param": "upper_threshold", "ladder": "threshold_upper", "monotonic": True},
    },
    "edges": {
        "row": _RANDOMNESS_ROW,
        "col": {"param": "lower_threshold", "ladder": "edge_threshold", "monotonic": True},
    },
    "file-edges": {
        "row": _RANDOMNESS_ROW,
        "col": {"param": "lower_threshold", "ladder": "edge_threshold", "monotonic": True},
    },
    "file": {
        "row": _RANDOMNESS_ROW,
        "col": {"param": "angle", "ladder": "angle_quadrant", "monotonic": False},
    },
    "none": {
        "row": _RANDOMNESS_ROW,
        "col": {"param": "angle", "ladder": "angle_quadrant", "monotonic": False},
    },
}

# --------------------------------------------------------------------------
# Matrix mode: the categorical cross-product.
#
# Excluded from the defaults on purpose:
#   file / file-edges  need a second uploaded image
#   none               sorts whole rows, so it makes a row of near-duplicates
#   minimum            reads close to intensity at grid scale
# All four remain selectable through the axis pickers.
# --------------------------------------------------------------------------
MATRIX_DEFAULT: dict = {
    "row_param": "interval_function",
    "col_param": "sorting_function",
    "row_values": {
        3: ["random", "threshold", "waves"],
        4: ["random", "threshold", "edges", "waves"],
    },
    "col_values": {
        3: ["lightness", "hue", "intensity"],
        4: ["lightness", "hue", "saturation", "intensity"],
    },
}

# --------------------------------------------------------------------------
# Sweep mode: one parameter across every cell.
# `space: log` because the interesting range of clength is bunched at the low end.
# --------------------------------------------------------------------------
SWEEPS: dict[str, dict] = {
    "angle":           {"space": "linear", "min": 0.0, "max": 337.5, "round": 1,
                        "label": "Angle", "unit": "\u00b0"},
    "clength":         {"space": "log",    "min": 5,   "max": 300,   "round": 0,
                        "label": "Char. length", "unit": "px"},
    "lower_threshold": {"space": "linear", "min": 0.0, "max": 0.9,   "round": 2,
                        "label": "Threshold (lower)", "unit": ""},
    "upper_threshold": {"space": "linear", "min": 0.55, "max": 1.0,  "round": 2,
                        "label": "Threshold (upper)", "unit": ""},
    "randomness":      {"space": "linear", "min": 0.0, "max": 90.0,  "round": 0,
                        "label": "Randomness", "unit": "%"},
}

# Seconds of CPU per megapixel, measured across 1.6 / 3.6 / 6.4 MP renders
# (6.9s, 13s, 27s). Used only for the pre-flight estimate.
SECONDS_PER_MEGAPIXEL = 4.0


# --------------------------------------------------------------------------
# Optional JSON override
# --------------------------------------------------------------------------
def _apply_overrides() -> None:
    path = config.CONTENT_DIR / "batch_ladders.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Ignoring %s: %s", path, exc)
        return

    for name, sizes in (data.get("ladders") or {}).items():
        # JSON object keys are strings; grid sizes are ints.
        LADDERS.setdefault(name, {}).update({int(k): v for k, v in sizes.items()})
    for ifn, axes in (data.get("intensity_axes") or {}).items():
        INTENSITY_AXES.setdefault(ifn, {}).update(axes)
    for name, cfg in (data.get("sweeps") or {}).items():
        SWEEPS.setdefault(name, {}).update(cfg)
    for key in ("row_values", "col_values"):
        if key in (data.get("matrix") or {}):
            MATRIX_DEFAULT[key].update(
                {int(k): v for k, v in data["matrix"][key].items()}
            )
    log.info("Applied batch ladder overrides from %s", path)


_apply_overrides()


# --------------------------------------------------------------------------
# Plan construction
# --------------------------------------------------------------------------
class PlanError(ValueError):
    """The requested grid cannot be built."""


def _ladder(name: str, size: int) -> list:
    try:
        return list(LADDERS[name][size])
    except KeyError:
        raise PlanError(f"No {size}-step ladder defined for {name!r}") from None


def _fmt(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def sweep_values(param: str, count: int) -> list:
    cfg = SWEEPS.get(param)
    if cfg is None:
        raise PlanError(f"{param!r} cannot be swept")
    lo, hi, digits = cfg["min"], cfg["max"], cfg["round"]
    out = []
    for i in range(count):
        t = i / (count - 1) if count > 1 else 0.0
        if cfg["space"] == "log":
            value = lo * (hi / lo) ** t
        else:
            value = lo + (hi - lo) * t
        out.append(round(value, digits) if digits else round(value))
    return out


def build_plan(mode: str, grid_size: int, options: dict) -> dict:
    """Return {rows, cols, axis metadata, cells} for the requested grid.

    Cells carry raw parameter dicts. Validation and flag construction happen in
    pixelsort_spec, so a plan can never contain a combination the CLI rejects.
    """
    if grid_size not in GRID_SIZES:
        raise PlanError(f"Grid size must be one of {GRID_SIZES}")

    constants = dict(options.get("constants") or {})
    n = grid_size

    if mode == "matrix":
        row_param = options.get("row_param") or MATRIX_DEFAULT["row_param"]
        col_param = options.get("col_param") or MATRIX_DEFAULT["col_param"]
        row_values = options.get("row_values") or MATRIX_DEFAULT["row_values"][n]
        col_values = options.get("col_values") or MATRIX_DEFAULT["col_values"][n]
        row_axis = {"param": row_param, "label": _axis_label(row_param), "monotonic": False}
        col_axis = {"param": col_param, "label": _axis_label(col_param), "monotonic": False}

    elif mode == "intensity":
        ifn = options.get("interval_function", "threshold")
        sfn = options.get("sorting_function", "lightness")
        axes = INTENSITY_AXES.get(ifn)
        if axes is None:
            raise PlanError(f"No intensity axes defined for {ifn!r}")
        constants["interval_function"] = ifn
        constants["sorting_function"] = sfn
        row_param, col_param = axes["row"]["param"], axes["col"]["param"]
        row_values = _ladder(axes["row"]["ladder"], n)
        col_values = _ladder(axes["col"]["ladder"], n)
        row_axis = {"param": row_param, "label": _axis_label(row_param),
                    "monotonic": axes["row"].get("monotonic", True)}
        col_axis = {"param": col_param, "label": _axis_label(col_param),
                    "monotonic": axes["col"].get("monotonic", True)}

    elif mode == "sweep":
        param = options.get("sweep_param", "angle")
        values = sweep_values(param, n * n)
        row_param = col_param = param
        row_values, col_values = None, None
        row_axis = col_axis = {"param": param, "label": _axis_label(param), "monotonic": True}

    else:
        raise PlanError(f"Unknown mode {mode!r}")

    cells = []
    if mode == "sweep":
        for index, value in enumerate(values):
            params = dict(constants)
            params[param] = value
            cells.append({
                "index": index, "row": index // n, "col": index % n,
                "row_label": "", "col_label": f"{_fmt(value)}{SWEEPS[param].get('unit','')}",
                "params": params,
            })
    else:
        if len(row_values) < n or len(col_values) < n:
            raise PlanError("Not enough axis values for the requested grid size")
        for r in range(n):
            for c in range(n):
                params = dict(constants)
                params[row_param] = row_values[r]
                params[col_param] = col_values[c]
                cells.append({
                    "index": r * n + c, "row": r, "col": c,
                    "row_label": _fmt(row_values[r]), "col_label": _fmt(col_values[c]),
                    "params": params,
                })

    return {
        "mode": mode,
        "grid_size": n,
        "row_axis": row_axis,
        "col_axis": col_axis,
        # Categorical axes (matrix mode) carry no unit; numeric ladders do.
        "row_labels": [_fmt(v) + (_unit(row_param) if mode != "matrix" else "")
                       for v in (row_values or [])],
        "col_labels": [_fmt(v) + (_unit(col_param) if mode != "matrix" else "")
                       for v in (col_values or [])],
        "cells": cells,
    }


_AXIS_LABELS = {
    "interval_function": "Interval function",
    "sorting_function": "Sorting function",
    "randomness": "Randomness",
    "clength": "Char. length",
    "lower_threshold": "Threshold (lower)",
    "upper_threshold": "Threshold (upper)",
    "angle": "Angle",
}


def _axis_label(param: str) -> str:
    return _AXIS_LABELS.get(param, param.replace("_", " ").title())


def _unit(param: str) -> str:
    """Suffix for numeric axis ticks, taken from the shared control metadata."""
    from . import pixelsort_spec
    return pixelsort_spec.CONTROLS.get(param, {}).get("unit", "")


def fill_order(n: int) -> list[int]:
    """Corner-first, then diagonal, then the rest.

    The corners span both axes' extremes, so the widest range of outcomes appears
    soonest and a bad source image can be abandoned after one cell instead of the
    whole batch.
    """
    order, seen = [], set()

    def push(r, c):
        i = r * n + c
        if 0 <= r < n and 0 <= c < n and i not in seen:
            seen.add(i)
            order.append(i)

    for r, c in ((0, 0), (0, n - 1), (n - 1, 0), (n - 1, n - 1)):
        push(r, c)
    for i in range(n):
        push(i, i)
        push(i, n - 1 - i)
    for r in range(n):
        for c in range(n):
            push(r, c)
    return order


def estimate_seconds(cell_count: int, megapixels: float, concurrency: int) -> int:
    total = cell_count * megapixels * SECONDS_PER_MEGAPIXEL
    return max(1, math.ceil(total / max(1, concurrency)))
