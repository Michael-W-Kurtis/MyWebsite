"""
Curated operation set for the PNGlitch wrapper.

Why a curated set rather than a wrapped CLI: pnglitch's command line takes one
option (`--filter`) and performs one hardcoded corruption
(`data.gsub /\\d/, 'x'`), which is six possible outputs in total. The library is
where the range lives, and its main entry point takes a Ruby block. So this
module generates that block from validated numeric and enumerated parameters.
The user never supplies code.

Two things were established by running the real library rather than reading
about it, and both shape the design:

1. **Filter type is a modifier, not an operation.** `change_all_filters` on its
   own is nearly lossless - the image comes back looking untouched, because
   re-filtering and re-encoding is a faithful round trip. What the filter type
   actually controls is how far damage *propagates*: with `none` a corrupted
   byte stays local, with `up` or `paeth` the error smears down every row
   beneath it. Same operation, wildly different results. So the filter is a
   separate control applied before the glitch.

2. **Byte offsets that ignore scanline boundaries destroy the image.** Writing
   over a per-line filter byte makes every row below it decode to black. The
   repeat and shuffle operations therefore use the scanline API
   (`each_scanline`, `replace_data`) instead of raw string offsets.

An operation that produced only black output at every setting (resizing the
IHDR width) was cut rather than shipped.
"""

from __future__ import annotations

import random

FILTER_TYPES = [
    {"value": "keep", "label": "keep original", "blurb": "Leave the filter types the image already has."},
    {"value": "none", "label": "none (0)", "blurb": "Damage stays where you put it."},
    {"value": "sub", "label": "sub (1)", "blurb": "Errors smear sideways along a row."},
    {"value": "up", "label": "up (2)", "blurb": "Errors run straight down the image."},
    {"value": "average", "label": "average (3)", "blurb": "Errors spread down and sideways."},
    {"value": "paeth", "label": "paeth (4)", "blurb": "Errors spread furthest. The most violent."},
]
FILTER_VALUES = [f["value"] for f in FILTER_TYPES]

# Enumerated so no user string ever reaches a Ruby regex.
PATTERNS = {
    "digits": {"label": "digits", "regex": r"\d"},
    "letters": {"label": "letters", "regex": r"[A-Za-z]"},
    "vowels": {"label": "vowels", "regex": r"[aeiou]"},
    "whitespace": {"label": "whitespace", "regex": r"\s"},
}
REPLACEMENTS = ["x", "z", "0", "#", "%", "."]


def _num(name, label, lo, hi, default, step=1, unit=""):
    return {"name": name, "label": label, "type": "range", "min": lo, "max": hi,
            "default": default, "step": step, "unit": unit}


OPERATIONS = [
    {
        "id": "substitute",
        "label": "Substitute",
        "blurb": "Find and replace inside the raw scanline bytes. This is the "
                 "operation pnglitch's own command line performs.",
        "seeded": False,
        "filterable": True,
        "params": [
            {"name": "pattern", "label": "Match", "type": "select",
             "options": [{"value": k, "label": v["label"]} for k, v in PATTERNS.items()],
             "default": "digits"},
            {"name": "replacement", "label": "Replace with", "type": "select",
             "options": [{"value": c, "label": c} for c in REPLACEMENTS],
             "default": "x"},
        ],
    },
    {
        "id": "random_bytes",
        "label": "Random bytes",
        "blurb": "Overwrite scattered individual bytes. Fine-grained speckle "
                 "that the filter type turns into streaks.",
        "seeded": True,
        "filterable": True,
        "params": [_num("count", "How many bytes", 1, 2000, 120)],
    },
    {
        "id": "chunk_swap",
        "label": "Chunk swap",
        "blurb": "Trade blocks of image data between two places. Blocky, "
                 "geometric displacement.",
        "seeded": True,
        "filterable": True,
        "params": [
            _num("swaps", "Swaps", 1, 60, 6),
            _num("block", "Block size", 32, 4096, 256, 32, "b"),
        ],
    },
    {
        "id": "scanline_repeat",
        "label": "Scanline repeat",
        "blurb": "Hold one row of pixels and stamp it over the rows beneath. "
                 "The classic stuttered-VHS band.",
        "seeded": False,
        "filterable": True,
        "params": [
            _num("start_pct", "Start at", 0, 95, 30, 1, "%"),
            _num("span", "Rows held", 1, 400, 40),
        ],
    },
    {
        "id": "scanline_shuffle",
        "label": "Scanline shuffle",
        "blurb": "Swap whole rows around. Keeps every pixel, moves them "
                 "vertically.",
        "seeded": True,
        "filterable": True,
        "params": [_num("swaps", "Row swaps", 1, 400, 30)],
    },
    {
        "id": "filter_band",
        "label": "Filter banding",
        "blurb": "Give each row a different filter type in rotation. Uses "
                 "grafting, so the pixels are untouched - only how they are "
                 "reconstructed changes.",
        "seeded": False,
        # This operation *is* the filter manipulation, so the modifier below is
        # meaningless here and the UI hides it.
        "filterable": False,
        "params": [_num("period", "Rows per band", 1, 64, 1)],
    },
    {
        "id": "after_compress",
        "label": "Corrupt compressed",
        "blurb": "Damage the deflate stream itself rather than the pixels. "
                 "Far more destructive - a handful of bytes is plenty.",
        "seeded": True,
        "filterable": True,
        "params": [_num("count", "Bytes hit", 1, 40, 3)],
    },
]

OPERATION_IDS = [o["id"] for o in OPERATIONS]
_BY_ID = {o["id"]: o for o in OPERATIONS}


class OperationError(ValueError):
    """A submitted operation or parameter is not one we generate code for."""


def normalize(raw: dict) -> dict:
    """Coerce untrusted form input into a parameter set we will generate from."""
    op_id = str(raw.get("operation", "substitute"))
    if op_id not in _BY_ID:
        raise OperationError(f"Unknown operation: {op_id!r}")
    op = _BY_ID[op_id]

    filter_type = str(raw.get("filter_type", "keep"))
    if filter_type not in FILTER_VALUES:
        raise OperationError(f"Unknown filter type: {filter_type!r}")
    if not op["filterable"]:
        filter_type = "keep"

    try:
        seed = int(raw.get("seed", 1))
    except (TypeError, ValueError):
        raise OperationError("Seed must be a whole number.") from None
    seed = max(0, min(2**31 - 1, seed))

    params: dict = {}
    for spec in op["params"]:
        value = raw.get(spec["name"], spec["default"])
        if spec["type"] == "select":
            value = str(value)
            allowed = [o["value"] for o in spec["options"]]
            if value not in allowed:
                raise OperationError(f"{spec['label']}: {value!r} is not one of {allowed}")
        else:
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise OperationError(f"{spec['label']} must be a number.") from None
            value = max(spec["min"], min(spec["max"], value))
        params[spec["name"]] = value

    return {"operation": op_id, "filter_type": filter_type, "seed": seed, "params": params}


def random_seed() -> int:
    return random.randint(1, 999_999)


def _body(op_id: str, p: dict, seed: int) -> str:
    """The Ruby for one operation. Every interpolated value is an int or an
    allow-listed constant, so nothing user-supplied is ever parsed as code."""
    if op_id == "substitute":
        regex = PATTERNS[p["pattern"]]["regex"]
        return (f'  png.glitch do |data|\n'
                f'    data.gsub(/{regex}/, {p["replacement"]!r})\n'
                f'  end')

    if op_id == "random_bytes":
        return (f'  png.glitch do |data|\n'
                f'    rng = Random.new({seed})\n'
                f'    {p["count"]}.times do\n'
                f'      data.setbyte(rng.rand(data.size), rng.rand(256))\n'
                f'    end\n'
                f'    data\n'
                f'  end')

    if op_id == "chunk_swap":
        return (f'  png.glitch do |data|\n'
                f'    rng = Random.new({seed})\n'
                f'    block = {p["block"]}\n'
                f'    blocks = data.size / block\n'
                f'    {p["swaps"]}.times do\n'
                f'      a = rng.rand(blocks) * block\n'
                f'      b = rng.rand(blocks) * block\n'
                f'      held = data[a, block]\n'
                f'      data[a, block] = data[b, block]\n'
                f'      data[b, block] = held\n'
                f'    end\n'
                f'    data\n'
                f'  end')

    if op_id == "scanline_repeat":
        return (f'  start_row = (png.height * {p["start_pct"]} / 100.0).to_i\n'
                f'  held = nil\n'
                f'  row = 0\n'
                f'  png.each_scanline do |line|\n'
                f'    held = line.data if row == start_row\n'
                f'    if !held.nil? && row > start_row && row <= start_row + {p["span"]}\n'
                f'      line.replace_data held\n'
                f'    end\n'
                f'    row += 1\n'
                f'  end')

    if op_id == "scanline_shuffle":
        return (f'  rows = []\n'
                f'  png.each_scanline {{ |line| rows << line.data }}\n'
                f'  rng = Random.new({seed})\n'
                f'  order = (0...rows.size).to_a\n'
                f'  {p["swaps"]}.times do\n'
                f'    a = rng.rand(rows.size)\n'
                f'    b = rng.rand(rows.size)\n'
                f'    order[a], order[b] = order[b], order[a]\n'
                f'  end\n'
                f'  row = 0\n'
                f'  png.each_scanline do |line|\n'
                f'    line.replace_data rows[order[row]]\n'
                f'    row += 1\n'
                f'  end')

    if op_id == "filter_band":
        return (f'  types = [0, 1, 2, 3, 4]\n'
                f'  row = 0\n'
                f'  png.each_scanline do |line|\n'
                f'    line.graft types[(row / {p["period"]}) % types.size]\n'
                f'    row += 1\n'
                f'  end')

    if op_id == "after_compress":
        return (f'  png.glitch_after_compress do |data|\n'
                f'    rng = Random.new({seed})\n'
                f'    {p["count"]}.times do\n'
                f'      data.setbyte(rng.rand(data.size), rng.rand(256))\n'
                f'    end\n'
                f'    data\n'
                f'  end')

    raise OperationError(f"No generator for {op_id!r}")


def build_script(config: dict, input_path: str, output_path: str) -> str:
    """The complete Ruby program. This exact text is what runs and what is shown."""
    lines = ["require 'pnglitch'", "", f"PNGlitch.open({input_path!r}) do |png|"]
    if config["filter_type"] != "keep":
        lines.append(f"  png.change_all_filters "
                     f"PNGlitch::Filter::{config['filter_type'].upper()}")
    lines.append(_body(config["operation"], config["params"], config["seed"]))
    lines.append(f"  png.save {output_path!r}")
    lines.append("end")
    return "\n".join(lines) + "\n"


def display_script(config: dict) -> str:
    """The same program with the real paths swapped for readable ones."""
    return build_script(config, "./input.png", "./glitched.png")


def spec_for_client() -> dict:
    return {
        "operations": OPERATIONS,
        "filterTypes": FILTER_TYPES,
        "patterns": PATTERNS,
        "replacements": REPLACEMENTS,
    }
