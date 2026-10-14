"""A tiny edit language a small model emits instead of rewriting the whole game file.

    set <path> = <yaml value>       replace a field, or create it
    add <path> = <yaml value>       append to a list
    remove <path>                   delete a field or a named list item

Paths use the benchmark syntax: `enemies[boss].behavior`, `characters[warrior].abilities[fireball].damage`.
"""
import copy
import re

import yaml

LINE = re.compile(r"^\s*(set|add|remove)\s+([A-Za-z_][\w\[\].]*?)\s*(?:=\s*(.+))?\s*$")


class EditError(ValueError):
    pass


def _tokens(path):
    toks = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)|\[([^\]]+)\]", path)
    if not toks or "".join(f".{k}" if k else f"[{s}]" for k, s in toks).lstrip(".") != path:
        raise EditError(f"bad path `{path}`")
    return [("key", k) if k else ("sel", s) for k, s in toks]


def _step(cur, tok, create=False):
    kind, v = tok
    if kind == "key":
        if not isinstance(cur, dict):
            raise EditError(f"`{v}`: parent is not a mapping")
        if v not in cur:
            if not create:
                raise EditError(f"no field `{v}`")
            cur[v] = {}
        return cur[v]
    if not isinstance(cur, list):
        raise EditError(f"[{v}]: parent is not a list")
    if v.isdigit():
        if int(v) >= len(cur):
            raise EditError(f"index {v} out of range")
        return cur[int(v)]
    hit = [x for x in cur if isinstance(x, dict) and x.get("name") == v]
    if not hit:
        raise EditError(f"no item named `{v}`")
    return hit[0]


def apply(data: dict, text: str) -> dict:
    """Apply edit lines to a game dict; returns a new dict. Raises EditError on any bad line."""
    d = copy.deepcopy(data)
    lines = [l for l in text.strip().splitlines() if l.strip() and not l.strip().startswith("#")]
    if not lines:
        raise EditError("no edits")
    for line in lines:
        m = LINE.match(line)
        if not m:
            raise EditError(f"cannot parse `{line.strip()}`")
        op, path, raw = m.groups()
        toks = _tokens(path)
        parent = d
        for t in toks[:-1]:
            parent = _step(parent, t, create=(op == "set"))
        last = toks[-1]
        if op == "remove":
            if raw is not None:
                raise EditError("remove takes no value")
            if last[0] == "key":
                if not isinstance(parent, dict) or last[1] not in parent:
                    raise EditError(f"no field `{last[1]}` to remove")
                del parent[last[1]]
            else:
                item = _step(parent, last)
                parent.remove(item)
            continue
        if raw is None:
            raise EditError(f"`{op}` needs `= value`")
        try:
            val = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            raise EditError(f"bad value `{raw}`: {e}")
        if op == "set":
            if last[0] == "key":
                if not isinstance(parent, dict):
                    raise EditError("parent is not a mapping")
                parent[last[1]] = val
            else:
                item = _step(parent, last)
                item.clear()
                item.update(val if isinstance(val, dict) else {})
        else:  # add
            target = _step(parent, last, create=True) if last[0] == "key" else _step(parent, last)
            if target == {} and last[0] == "key":
                parent[last[1]] = target = []
            if not isinstance(target, list):
                raise EditError(f"`{path}` is not a list")
            target.append(val)
    return d
