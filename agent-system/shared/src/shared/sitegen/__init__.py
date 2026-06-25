"""sitegen — deterministic, template-driven site generation for the web-agent.

The web-agent's slow path asks Claude to write a whole HTML document from scratch (~150s of
streaming, with the ever-present risk of generic "AI slop"). sitegen is the fast path: a small
set of hand-built, on-brand, single-file HTML templates, each with a JSON manifest declaring its
configurable fields. The model's job collapses to *picking a template and emitting a small JSON
config*; `render()` fills defaults and substitutes tokens deterministically (see `_mustache.py`).
No long stream, no slop, identical output every time.

Public API:
  catalog()                       -> [TemplateSpec, …]  (id, name, description, field schema)
  catalog_for_prompt()            -> compact JSON str the model picks from
  render(template_id, config)     -> finished HTML (defaults filled, tokens substituted)

Templates live in `templates/<id>/{manifest.json, template.html}` and ship as package data, so
this resolves the same way in editable dev installs and the Docker image (path is relative to
this file). Adding a template = adding a directory; nothing here needs to change.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from functools import lru_cache

from ._mustache import render as _render

_TEMPLATES_DIR = pathlib.Path(__file__).parent / "templates"


class TemplateError(ValueError):
    """Unknown template id, or a template directory that is missing/malformed."""


@dataclass(frozen=True)
class TemplateSpec:
    """A template's identity + field schema — everything the model needs to choose and fill it."""

    id: str
    name: str
    description: str
    fields: dict          # field-name -> {type, description, default?, required?, item?}
    _html: str            # the raw template source (kept private; render() uses it)

    def public(self) -> dict:
        """The catalog view shown to the model — schema only, no template body."""
        return {"id": self.id, "name": self.name, "description": self.description,
                "fields": self.fields}


@lru_cache(maxsize=1)
def _load_all() -> dict[str, TemplateSpec]:
    """Discover and parse every template once. A malformed dir is skipped, not fatal — one bad
    template must never take down the whole catalog."""
    specs: dict[str, TemplateSpec] = {}
    if not _TEMPLATES_DIR.is_dir():
        return specs
    for d in sorted(_TEMPLATES_DIR.iterdir()):
        manifest = d / "manifest.json"
        html_file = d / "template.html"
        if not (d.is_dir() and manifest.exists() and html_file.exists()):
            continue
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
            specs[d.name] = TemplateSpec(
                id=m.get("id", d.name),
                name=m["name"],
                description=m["description"],
                fields=m.get("fields", {}),
                _html=html_file.read_text(encoding="utf-8"),
            )
        except (json.JSONDecodeError, KeyError, OSError):
            continue
    return specs


def catalog() -> list[TemplateSpec]:
    """All available templates (stable order by id)."""
    return list(_load_all().values())


def catalog_for_prompt() -> str:
    """Compact JSON of the catalog (schema only) for the model's template-selection prompt."""
    return json.dumps([s.public() for s in catalog()], separators=(",", ":"))


def get(template_id: str) -> TemplateSpec:
    spec = _load_all().get(template_id)
    if spec is None:
        raise TemplateError(
            f"unknown template '{template_id}'; available: {sorted(_load_all())}")
    return spec


def _is_empty(val) -> bool:
    """An explicit 'turn this off' value: null, empty string, or empty list/dict."""
    return val is None or val == "" or (isinstance(val, (list, dict)) and len(val) == 0)


def _fill_defaults(fields: dict, config: dict) -> dict:
    """Merge `config` over each field's declared default, so a partial config still renders.

    Three cases, chosen to give the model a clean way to control optional blocks:
      • key omitted          → use the field's declared default (so a bare config renders full)
      • key present + empty   → leave it empty (null/""/[]); the template section is skipped
      • key present + value   → use it (list-item defaults are merged in per item)
    Unknown config keys pass through untouched (a template may read tokens not formally declared)."""
    merged = dict(config)
    for name, spec in fields.items():
        if name in merged:
            val = merged[name]
            if _is_empty(val):
                merged[name] = None  # explicit off — do NOT fall back to the default
                continue
            if spec.get("type") == "list" and isinstance(val, list) and isinstance(spec.get("item"), dict):
                item_defaults = {k: v.get("default") for k, v in spec["item"].items() if "default" in v}
                merged[name] = [{**item_defaults, **it} if isinstance(it, dict) else it for it in val]
            continue
        if "default" in spec:
            merged[name] = spec["default"]

    # Companion booleans for every list field: `has_<field>` is true iff the field resolved to a
    # non-empty list. Lets a template render a whole section ONCE only when its list has items
    # (`{{#has_speakers}}<section>…{{#speakers}}…{{/speakers}}</section>{{/has_speakers}}`) — the
    # one thing logic-less Mustache can't express on its own.
    for name, spec in fields.items():
        if spec.get("type") == "list":
            merged[f"has_{name}"] = bool(merged.get(name))
    return merged


def render(template_id: str, config: dict | None = None) -> str:
    """Render `template_id` with `config` (LLM-produced), filling declared defaults for omissions.

    Deterministic and pure: same (id, config) always yields the same HTML. Raises TemplateError
    for an unknown id; never raises on a sparse/partial config (that's the whole point of defaults).
    """
    spec = get(template_id)
    ctx = _fill_defaults(spec.fields, config or {})
    return _render(spec._html, ctx)
