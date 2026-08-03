from __future__ import annotations

import hashlib
from pathlib import Path
import sys
from collections.abc import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.repo_model import SourceRecord, load_manifest


USER_AGENT = "human-translation-skills-source-verifier/1.0"
TIMEOUT_SECONDS = 20
Fetcher = Callable[[SourceRecord], bytes]


def raw_url(source: SourceRecord) -> str:
    return (
        f"https://raw.githubusercontent.com/{source.repository}/"
        f"{source.ref}/{source.path}"
    )


def verify_content(source: SourceRecord, content: bytes) -> str | None:
    actual = hashlib.sha256(content).hexdigest()
    if actual == source.sha256:
        return None
    return f"{source.id}: expected {source.sha256}, received {actual}"


def validate_upstream_skill(
    source: SourceRecord,
    content: bytes,
) -> str | None:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return f"{source.id}: upstream SKILL.md is not UTF-8"
    if not lines or lines[0] != "---":
        return f"{source.id}: invalid frontmatter delimiters"
    try:
        end = lines.index("---", 1)
    except ValueError:
        return f"{source.id}: invalid frontmatter delimiters"

    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    for required in ("name", "description"):
        if not fields.get(required):
            return f"{source.id}: missing {required}"
    return None


def validate_source_format(
    source: SourceRecord,
    content: bytes,
) -> str | None:
    if Path(source.path).name == "SKILL.md":
        return validate_upstream_skill(source, content)
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return f"{source.id}: upstream reference is not UTF-8"
    return None


def fetch(source: SourceRecord) -> bytes:
    request = Request(
        raw_url(source),
        headers={"User-Agent": USER_AGENT},
    )
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def verify_source(
    source: SourceRecord,
    *,
    fetcher: Fetcher = fetch,
) -> str | None:
    try:
        content = fetcher(source)
    except HTTPError as exception:
        error = f"{source.id}: HTTP {exception.code}: {exception.reason}"
        exception.close()
        return error
    except URLError as exception:
        return f"{source.id}: network failure: {exception.reason}"
    except (TimeoutError, OSError) as exception:
        return f"{source.id}: network failure: {exception}"

    error = verify_content(source, content)
    if error is not None:
        return error
    return validate_source_format(source, content)


def verify_sources(
    sources: Iterable[SourceRecord],
    *,
    fetcher: Fetcher = fetch,
) -> list[str]:
    return [
        error
        for source in sources
        if (error := verify_source(source, fetcher=fetcher)) is not None
    ]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    manifest = load_manifest(root / "skills-manifest.json")
    errors = verify_sources(manifest.sources)
    if errors:
        print("\n".join(errors))
        return 1
    print(f"verified {len(manifest.sources)} pinned sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
