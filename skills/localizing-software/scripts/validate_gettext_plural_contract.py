#!/usr/bin/env python3
"""Check the sampled plural behavior of a compiled GNU gettext catalog."""
from __future__ import annotations

import argparse
import gettext
import json
from pathlib import Path
import re
import struct
import sys


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def load_authority(path: Path) -> tuple[int, dict[int, int], list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(data, dict) or not {"nplurals", "selections"} <= set(data) <= {
        "nplurals", "selections", "required_messages"
    }:
        raise ValueError("expected nplurals, selections and optional required_messages")
    arity, selections = data["nplurals"], data["selections"]
    if type(arity) is not int or arity < 1:
        raise ValueError("nplurals must be a positive integer")
    if not isinstance(selections, dict) or not selections:
        raise ValueError("selections must map at least one count to a branch index")
    indices = {}
    for count, branch in selections.items():
        if not re.fullmatch(r"0|[1-9][0-9]*", count):
            raise ValueError("counts must be canonical non-negative decimal integers")
        if type(branch) is not int or not 0 <= branch < arity:
            raise ValueError(f"invalid branch index for count {count}")
        indices[int(count)] = branch
    messages = data.get("required_messages", [])
    if not isinstance(messages, list):
        raise ValueError("required_messages must be a list")
    keys = []
    for message in messages:
        if not isinstance(message, dict) or not {"msgid"} <= set(message) <= {"msgid", "context"}:
            raise ValueError("each required message must have msgid and optional context")
        msgid, context = message["msgid"], message.get("context")
        if not isinstance(msgid, str) or not msgid or "\x04" in msgid:
            raise ValueError("required message has an invalid msgid")
        if context is not None and (not isinstance(context, str) or "\x04" in context):
            raise ValueError("required message has an invalid context")
        keys.append(msgid if context is None else f"{context}\x04{msgid}")
    if len(keys) != len(set(keys)):
        raise ValueError("required_messages must not contain duplicates")
    return arity, indices, keys


def check_catalog(
    catalog: gettext.GNUTranslations, arity: int, selections: dict[int, int], required_messages: list[str]
) -> list[str]:
    rule = catalog.info().get("plural-forms", "")
    arities = re.findall(r"(?:^|;)\s*nplurals\s*=\s*([0-9]+)\s*(?=;|$)", rule)
    selectors = re.findall(r"(?:^|;)\s*plural\s*=\s*[^;]+", rule)
    if len(arities) != 1 or len(selectors) != 1:
        return ["catalog requires a single explicit plural arity and selector"]
    if int(arities[0]) != arity:
        return [f"plural arity differs: expected {arity}, found {arities[0]}"]
    errors = []
    for count, expected in sorted(selections.items()):
        actual = catalog.plural(count)
        if actual != expected:
            errors.append(f"count {count}: expected branch {expected}, found {actual}")
    # Check stored forms since ngettext falls back to the source for absent keys.
    for number, key in enumerate(required_messages, start=1):
        for branch in range(arity):
            if not catalog._catalog.get((key, branch)):
                errors.append(f"required message {number} has no translation for branch {branch}")
                break
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path, help="compiled GNU .mo file")
    parser.add_argument("authority", type=Path, help="approved arity and sampled count indices")
    args = parser.parse_args()
    try:
        arity, selections, required_messages = load_authority(args.authority)
    except (OSError, ValueError, RecursionError) as error:
        print(f"invalid authority: {error}", file=sys.stderr)
        return 2
    try:
        with args.catalog.open("rb") as stream:
            catalog = gettext.GNUTranslations(stream)
        errors = check_catalog(catalog, arity, selections, required_messages)
    except (OSError, ValueError, LookupError, ArithmeticError, struct.error) as error:
        print(f"invalid catalog: {error}", file=sys.stderr)
        return 1
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(json.dumps({"checked_counts": len(selections), "nplurals": arity}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
