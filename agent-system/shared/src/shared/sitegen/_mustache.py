"""A tiny logic-less template renderer — a deliberate subset of Mustache, zero dependencies.

Why not Jinja2 or str.format: site templates are authored by humans and rendered from an
LLM-produced JSON config, so the engine must be (1) safe against arbitrary config text (HTML
escape by default), (2) blind to the single braces that pepper CSS (`a { color: ... }`), and
(3) trivial to read in a template file. Mustache's `{{ }}` delimiters and logic-less sections
hit all three; we implement only the handful of constructs the templates actually use.

Supported:
  {{ key }}          escaped scalar (HTML-escaped — the default for all copy)
  {{{ key }}}        raw scalar (no escaping — for colors, pre-vetted HTML/SVG snippets)
  {{& key }}         raw scalar (Mustache's other unescaped form; same as triple-brace)
  {{# key }}…{{/ key }}   section — list → repeat block per item; truthy scalar/dict → render once;
                          falsy/empty/missing → skip
  {{^ key }}…{{/ key }}   inverted section — render only when the value is falsy/empty/missing
  {{! comment }}     stripped

Lookup walks a context stack (inner section item first, then enclosing scopes), so `{{brand}}`
resolves inside a `{{#features}}` loop. `{{.}}` is the current item itself (for lists of scalars).
Dotted keys (`{{a.b}}`) walk nested dicts. This is intentionally NOT full Mustache: no partials,
no lambdas, no set-delimiter. If a template needs more, the template is too clever.
"""

from __future__ import annotations

import html
import re

# One tag matcher. Triple-brace must be tried before double-brace, hence the leading `\{?`
# capture: a present inner `{` (with matching `}` before `}}`) means raw. `key` allows word
# chars, dots, and the lone `.` (current item); comments (`!`) swallow the rest of the tag.
_TAG = re.compile(
    r"\{\{"
    r"(?P<sigil>[#^/&!]?)"          # section / inverted / close / unescaped / comment — or none
    r"\s*"
    r"(?P<key>\{?\s*[\w.]+\s*\}?|!.*?|.*?)"  # key (optionally wrapped in a third brace) or comment body
    r"\s*\}\}",
    re.DOTALL,
)


class _Section:
    __slots__ = ("key", "inverted", "nodes")

    def __init__(self, key: str, inverted: bool) -> None:
        self.key = key
        self.inverted = inverted
        self.nodes: list = []


def _parse(template: str) -> list:
    """Tokenize then fold sections into a tree of (str | ('var', key, raw) | _Section)."""
    root: list = []
    stack: list[_Section] = []
    current = root
    pos = 0

    def emit(node) -> None:
        current.append(node)

    for m in _TAG.finditer(template):
        if m.start() > pos:
            emit(template[pos:m.start()])
        pos = m.end()
        sigil = m.group("sigil")
        key = m.group("key").strip()

        if sigil == "!":  # comment — drop
            continue
        if sigil in ("#", "^"):
            sec = _Section(key, inverted=(sigil == "^"))
            emit(sec)
            stack.append(sec)
            current = sec.nodes
            continue
        if sigil == "/":
            if not stack or stack[-1].key != key:
                raise ValueError(f"mismatched section close: {{{{/{key}}}}}")
            stack.pop()
            current = stack[-1].nodes if stack else root
            continue
        # variable: raw if {{{…}}} (inner brace) or {{& …}}
        raw = sigil == "&" or (key.startswith("{") and key.endswith("}"))
        key = key.strip("{} ").strip()
        emit(("var", key, raw))

    if stack:
        raise ValueError(f"unclosed section: {{{{#{stack[-1].key}}}}}")
    if pos < len(template):
        emit(template[pos:])
    return root


_MISSING = object()


def _lookup(stack: list, key: str):
    """Resolve `key` against the context stack (innermost first). `.` is the current item."""
    if key == ".":
        return stack[-1] if stack else _MISSING
    head, _, rest = key.partition(".")
    for ctx in reversed(stack):
        if isinstance(ctx, dict) and head in ctx:
            val = ctx[head]
            for part in rest.split(".") if rest else []:
                if isinstance(val, dict) and part in val:
                    val = val[part]
                else:
                    return _MISSING
            return val
    return _MISSING


def _truthy(val) -> bool:
    """Mustache emptiness: missing / None / False / empty list/str/dict are falsy."""
    if val is _MISSING or val is None or val is False:
        return False
    if isinstance(val, (list, str, dict)) and len(val) == 0:
        return False
    return True


def _render_nodes(nodes: list, stack: list, out: list) -> None:
    for node in nodes:
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, tuple):  # ('var', key, raw)
            _, key, raw = node
            val = _lookup(stack, key)
            if val is _MISSING or val is None or val is False:
                continue
            text = val if isinstance(val, str) else ("" if val is True else str(val))
            out.append(text if raw else html.escape(text, quote=True))
        else:  # _Section
            val = _lookup(stack, node.key)
            if node.inverted:
                if not _truthy(val):
                    _render_nodes(node.nodes, stack, out)
                continue
            if not _truthy(val):
                continue
            if isinstance(val, list):
                for item in val:
                    _render_nodes(node.nodes, stack + [item], out)
            elif isinstance(val, dict):
                _render_nodes(node.nodes, stack + [val], out)
            else:  # truthy scalar — render once with the current scope
                _render_nodes(node.nodes, stack, out)


def render(template: str, context: dict) -> str:
    """Render a Mustache-subset `template` against `context` (a dict). Pure; no I/O."""
    out: list[str] = []
    _render_nodes(_parse(template), [context], out)
    return "".join(out)
