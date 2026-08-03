from __future__ import annotations

import hashlib
import io
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError

from scripts.repo_model import SourceRecord, load_manifest
from scripts.verify_sources import (
    raw_url,
    validate_source_format,
    validate_upstream_skill,
    verify_content,
    verify_source,
)


ROOT = Path(__file__).resolve().parents[1]


def source_record(
    *,
    source_id: str = "demo",
    path: str = "skills/demo/SKILL.md",
    content: bytes = b"pinned source\n",
) -> SourceRecord:
    return SourceRecord(
        id=source_id,
        repository="owner/repo",
        ref="a" * 40,
        path=path,
        license="MIT",
        sha256=hashlib.sha256(content).hexdigest(),
        mode="adapted",
        capabilities=("core-translation",),
        adapter="adapters/demo.md",
    )


class SourceLockTests(unittest.TestCase):
    def setUp(self):
        self.content = b"pinned source\n"
        self.source = source_record(content=self.content)

    def test_raw_url_uses_immutable_ref(self):
        self.assertEqual(
            raw_url(self.source),
            "https://raw.githubusercontent.com/owner/repo/"
            + "a" * 40
            + "/skills/demo/SKILL.md",
        )

    def test_matching_content_passes(self):
        self.assertIsNone(verify_content(self.source, self.content))

    def test_mismatch_names_source_and_both_hashes(self):
        error = verify_content(self.source, b"changed\n")
        self.assertIsNotNone(error)
        self.assertIn("demo", error)
        self.assertIn(self.source.sha256, error)
        self.assertIn(hashlib.sha256(b"changed\n").hexdigest(), error)

    def test_upstream_skill_requires_utf8_frontmatter_name_and_description(self):
        valid = (
            b"---\nname: demo\ndescription: Demo skill. Use for demos.\n"
            b"---\n\n# Demo\n"
        )
        self.assertIsNone(validate_upstream_skill(self.source, valid))
        self.assertIn(
            "missing description",
            validate_upstream_skill(self.source, b"---\nname: demo\n---\n"),
        )
        self.assertIn(
            "not UTF-8",
            validate_upstream_skill(self.source, b"\xff\xfe"),
        )

    def test_only_skill_contract_paths_require_skill_frontmatter(self):
        reference = source_record(path="references/translation-guide.md")
        self.assertIsNone(validate_source_format(reference, b"# Translation guide\n"))
        self.assertIn(
            "not UTF-8",
            validate_source_format(reference, b"\xff\xfe"),
        )
        self.assertIn(
            "invalid frontmatter",
            validate_source_format(self.source, b"# Not a skill\n"),
        )

    def test_http_failure_is_distinct_from_network_failure(self):
        class TrackingHTTPError(HTTPError):
            closed_by_verifier = False

            def close(self):
                self.closed_by_verifier = True
                super().close()

        response = io.BytesIO(b"not found")
        http_error = TrackingHTTPError(
            raw_url(self.source), 404, "Not Found", {}, response
        )

        def http_failure(_source: SourceRecord) -> bytes:
            raise http_error

        def network_failure(_source: SourceRecord) -> bytes:
            raise URLError("temporary DNS failure")

        self.assertEqual(
            verify_source(self.source, fetcher=http_failure),
            "demo: HTTP 404: Not Found",
        )
        self.assertTrue(http_error.closed_by_verifier)
        self.assertEqual(
            verify_source(self.source, fetcher=network_failure),
            "demo: network failure: temporary DNS failure",
        )

    def test_verify_source_checks_format_only_after_checksum_matches(self):
        valid = (
            b"---\nname: demo\ndescription: Demo skill. Use for demos.\n"
            b"---\n\n# Demo\n"
        )
        source = source_record(content=valid)
        self.assertIsNone(verify_source(source, fetcher=lambda _source: valid))

        malformed = b"# Missing frontmatter\n"
        malformed_source = source_record(content=malformed)
        self.assertIn(
            "invalid frontmatter",
            verify_source(malformed_source, fetcher=lambda _source: malformed),
        )

    def test_adapters_declare_every_source_contract(self):
        manifest = load_manifest(ROOT / "skills-manifest.json")
        for source in manifest.sources:
            text = (ROOT / source.adapter).read_text(encoding="utf-8")
            self.assertIn(source.id, text, source.id)
            self.assertIn(f"https://github.com/{source.repository}", text, source.id)
            self.assertIn(source.path, text, source.id)
            self.assertIn(source.ref, text, source.id)
            self.assertIn(source.license, text, source.id)
            for capability in source.capabilities:
                self.assertIn(capability, text, source.id)


if __name__ == "__main__":
    unittest.main()
