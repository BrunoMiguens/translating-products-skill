from __future__ import annotations

import copy
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.benchmark.common import BenchmarkError, canonical_bytes, read_json, read_jsonl
from scripts.benchmark.review_app import (
    MAX_BODY_BYTES,
    CSP,
    ReviewStore,
    _parse_json_bytes,
    create_server,
    validate_annotation,
)


def item(item_id: str = "item-1", *, three_outputs: bool = True) -> dict:
    outputs = {"A": "Pagar agora", "B": "Efetuar o pagamento"}
    if three_outputs:
        outputs["C"] = "Paga já 😊"
    return {
        "id": item_id,
        "source": "Pay now",
        "context": {"surface": "checkout", "audience": "consumer"},
        "constraints": ["Use European Portuguese"],
        "outputs": outputs,
    }


def bundle(*items: dict) -> dict:
    return {"schema_version": 1, "items": list(items)}


def valid_annotation(review_item: dict, *, revision: int = 1) -> dict:
    labels = list(review_item["outputs"])
    comparisons = {
        f"{left}:{right}": "tie"
        for index, left in enumerate(labels)
        for right in labels[index + 1 :]
    }
    return {
        "item_id": review_item["id"],
        "revision": revision,
        "comparisons": comparisons,
        "confidence": "high",
        "mqm": [],
        "major_or_worse": {label: False for label in labels},
        "note": "Natural product wording.",
    }


def reviewer_attestation() -> dict:
    return {
        "schema_version": 1,
        "reviewer_locale": "pt-PT",
        "pt_pt_proficient": True,
        "independence_and_conflicts": "Independent reviewer; no conflicts declared.",
        "continued_blindness_acknowledged": True,
        "condition_key_not_accessed": True,
        "automated_findings_not_accessed": True,
        "rubric_completed": True,
    }


def nested_json(depth: int) -> bytes:
    return b"[" * depth + b"0" + b"]" * depth


class AnnotationValidationTests(unittest.TestCase):
    def test_three_way_comparison_cycle_is_rejected_before_persistence(self):
        """Break: A>B, B>C, C>A could be saved and make a locked review unscoreable."""
        review_item = item()
        event = valid_annotation(review_item)
        event["comparisons"] = {
            "A:B": "left_clear",
            "A:C": "right_clear",
            "B:C": "left_clear",
        }

        with self.assertRaisesRegex(BenchmarkError, "three-way comparison cycle"):
            validate_annotation(review_item, event)

    def test_annotation_requires_every_pair_confidence_and_exact_fields(self):
        review_item = item()
        event = valid_annotation(review_item)
        validate_annotation(review_item, event)

        missing = copy.deepcopy(event)
        del missing["comparisons"]["A:C"]
        with self.assertRaisesRegex(BenchmarkError, "missing comparison A:C"):
            validate_annotation(review_item, missing)

        unknown = copy.deepcopy(event)
        unknown["condition"] = "suite"
        with self.assertRaisesRegex(BenchmarkError, "invalid annotation fields"):
            validate_annotation(review_item, unknown)

        invalid_confidence = copy.deepcopy(event)
        invalid_confidence["confidence"] = "certain"
        with self.assertRaisesRegex(BenchmarkError, "confidence"):
            validate_annotation(review_item, invalid_confidence)

    def test_mqm_requires_exact_anonymous_output_enum_and_python_string_span(self):
        review_item = item()
        event = valid_annotation(review_item)
        event["mqm"] = [{
            "output": "C",
            "dimension": "locale_audience",
            "severity": "major",
            "start": 8,
            "end": 9,
            "note": "The emoji is unsuitable here.",
        }]
        event["major_or_worse"]["C"] = True
        validate_annotation(review_item, event)

        beyond_python_length = copy.deepcopy(event)
        beyond_python_length["mqm"][0]["end"] = 10
        with self.assertRaisesRegex(BenchmarkError, "span"):
            validate_annotation(review_item, beyond_python_length)

        invalid_dimension = copy.deepcopy(event)
        invalid_dimension["mqm"][0]["dimension"] = "grammar"
        with self.assertRaisesRegex(BenchmarkError, "dimension"):
            validate_annotation(review_item, invalid_dimension)

        wrong_flag = copy.deepcopy(event)
        wrong_flag["major_or_worse"]["C"] = False
        with self.assertRaisesRegex(BenchmarkError, "major_or_worse C"):
            validate_annotation(review_item, wrong_flag)

    def test_major_or_worse_requires_every_output_and_real_booleans(self):
        review_item = item()
        event = valid_annotation(review_item)
        del event["major_or_worse"]["B"]
        with self.assertRaisesRegex(BenchmarkError, "major_or_worse fields"):
            validate_annotation(review_item, event)

        event = valid_annotation(review_item)
        event["major_or_worse"]["A"] = 0
        with self.assertRaisesRegex(BenchmarkError, "major_or_worse A"):
            validate_annotation(review_item, event)

    def test_enum_values_require_exact_unicode_text_before_membership(self):
        review_item = item()
        mutations = (
            ("comparison", ["tie"], "comparison A:B"),
            ("confidence", [], "confidence"),
            ("dimension", {}, "dimension"),
            ("severity", ["major"], "severity"),
            ("confidence", "\ud800", "confidence"),
        )
        for field, malformed, message in mutations:
            with self.subTest(field=field, malformed=repr(malformed)):
                event = valid_annotation(review_item)
                if field == "comparison":
                    event["comparisons"]["A:B"] = malformed
                elif field == "confidence":
                    event["confidence"] = malformed
                else:
                    event["mqm"] = [{
                        "output": "A",
                        "dimension": "accuracy",
                        "severity": "minor",
                        "start": 0,
                        "end": 1,
                        "note": "Finding",
                    }]
                    event["mqm"][0][field] = malformed
                with self.assertRaisesRegex(BenchmarkError, message):
                    validate_annotation(review_item, event)


class ReviewJsonBoundaryTests(unittest.TestCase):
    def test_parser_accepts_exact_integer_and_nesting_limits(self):
        for token in (b"7" * 4096, b"-" + b"7" * 4096):
            with self.subTest(sign=token[:1]):
                parsed = _parse_json_bytes(token + b"\n", "boundary JSON")
                self.assertEqual(parsed < 0, token.startswith(b"-"))

        parsed = _parse_json_bytes(nested_json(256) + b"\n", "boundary JSON")
        depth = 0
        while isinstance(parsed, list):
            depth += 1
            parsed = parsed[0]
        self.assertEqual((depth, parsed), (256, 0))

    def test_parser_rejects_large_integers_deep_nesting_and_library_resource_errors(self):
        for token in (b"8" * 5000, b"-" + b"8" * 5000):
            with self.subTest(sign=token[:1]):
                with self.assertRaisesRegex(BenchmarkError, "integer exceeds 4096 digits"):
                    _parse_json_bytes(token + b"\n", "bounded JSON")

        with self.assertRaisesRegex(BenchmarkError, "nesting exceeds 256 levels"):
            _parse_json_bytes(nested_json(257) + b"\n", "bounded JSON")
        with self.assertRaisesRegex(BenchmarkError, "duplicate JSON field"):
            _parse_json_bytes(b'{"x":1,"x":2}\n', "bounded JSON")

        for error in (
            ValueError("plain value failure"),
            RecursionError("recursive failure"),
            OverflowError("overflow failure"),
            UnicodeError("unicode failure"),
        ):
            with self.subTest(error=type(error).__name__):
                with patch("scripts.benchmark.review_app.json.loads", side_effect=error):
                    with self.assertRaisesRegex(BenchmarkError, "invalid bounded JSON"):
                        _parse_json_bytes(b"{}\n", "bounded JSON")


class ReviewStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_lock_requires_protocol_attestation_and_binds_it_to_the_bundle(self):
        """Break: a lock could claim human review without evidence the reviewer followed protocol."""
        store = ReviewStore(bundle(item()), self.directory)
        store.append(valid_annotation(store.item("item-1")))
        with self.assertRaisesRegex(BenchmarkError, "reviewer attestation"):
            store.lock(reviewer_id="pt-PT-reviewer")

        lock = store.lock(
            reviewer_id="pt-PT-reviewer", attestation=reviewer_attestation()
        )
        self.assertEqual(lock["attestation"]["reviewer_locale"], "pt-PT")
        self.assertEqual(lock["attestation"]["bundle_sha256"], lock["bundle_sha256"])

    def test_revisions_append_and_lock_refuses_incomplete_queue(self):
        review_bundle = bundle(item("item-1"), item("item-2", three_outputs=False))
        store = ReviewStore(review_bundle, self.directory)
        first = store.append(valid_annotation(store.item("item-1")))
        revised = store.append(valid_annotation(store.item("item-1"), revision=2))

        self.assertGreater(revised["sequence"], first["sequence"])
        self.assertEqual([record["revision"] for record in read_jsonl(store.events_path)], [1, 2])
        self.assertEqual(store.latest["item-1"]["revision"], 2)
        with self.assertRaisesRegex(BenchmarkError, "1 presentation remains"):
            store.lock(reviewer_id="reviewer-1", attestation=reviewer_attestation())

    def test_revision_cannot_overwrite_or_skip_and_unknown_items_are_rejected(self):
        store = ReviewStore(bundle(item()), self.directory)
        store.append(valid_annotation(store.item("item-1")))

        with self.assertRaisesRegex(BenchmarkError, "expected revision 2"):
            store.append(valid_annotation(store.item("item-1")))
        with self.assertRaisesRegex(BenchmarkError, "expected revision 2"):
            store.append(valid_annotation(store.item("item-1"), revision=3))

        unknown = valid_annotation(item("unknown"))
        with self.assertRaisesRegex(BenchmarkError, "unknown item"):
            store.append(unknown)
        self.assertEqual(len(read_jsonl(store.events_path)), 1)

    def test_recovery_rebuilds_state_but_rejects_an_invalid_event_history(self):
        review_bundle = bundle(item())
        store = ReviewStore(review_bundle, self.directory)
        stored = store.append(valid_annotation(store.item("item-1")))
        stale_state = store.public_state()
        stale_state.update({
            "completed": 0,
            "remaining": 1,
            "last_sequence": 0,
            "latest": {},
        })
        store.state_path.write_bytes(canonical_bytes(stale_state))

        recovered = ReviewStore(review_bundle, self.directory)
        self.assertEqual(recovered.latest["item-1"], stored)
        self.assertEqual(read_json(recovered.state_path), recovered.public_state())

        records = read_jsonl(recovered.events_path)
        records[0]["sequence"] = 7
        recovered.events_path.write_bytes(canonical_bytes(records[0]))
        with self.assertRaisesRegex(BenchmarkError, "expected sequence 1"):
            ReviewStore(review_bundle, self.directory)

    def test_replay_requires_exact_integer_and_canonical_timestamp_types(self):
        mutations = (
            ("sequence", True, "sequence"),
            ("revision", True, "revision"),
            ("saved_at", "2026-08-05T12:00:00.000Z", "saved_at"),
        )
        for index, (field, malformed, message) in enumerate(mutations):
            with self.subTest(field=field):
                directory = self.directory / f"event-{index}"
                directory.mkdir()
                store = ReviewStore(bundle(item()), directory)
                store.append(valid_annotation(store.item("item-1")))
                record = read_jsonl(store.events_path)[0]
                record[field] = malformed
                store.events_path.write_bytes(canonical_bytes(record))
                store.close()
                with self.assertRaisesRegex(BenchmarkError, message):
                    ReviewStore(bundle(item()), directory)

    def test_malformed_state_and_lock_metadata_fail_closed(self):
        state_directory = self.directory / "state"
        state_directory.mkdir()
        state_store = ReviewStore(bundle(item()), state_directory)
        malformed_state = state_store.public_state()
        malformed_state["schema_version"] = True
        state_store.state_path.write_bytes(canonical_bytes(malformed_state))
        state_store.close()
        with self.assertRaisesRegex(BenchmarkError, "state schema_version"):
            ReviewStore(bundle(item()), state_directory)

        mutations = (
            ("schema_version", True, "schema version"),
            ("reviewer_id", " padded ", "reviewer_id"),
            ("reviewer_id", "\ud800", "invalid annotation lock"),
            ("locked_at", "not-a-time", "locked_at"),
            ("locked_at", "2026-08-05T12:00:00.000Z", "locked_at"),
        )
        for index, (field, malformed, message) in enumerate(mutations):
            with self.subTest(field=field, malformed=repr(malformed)):
                directory = self.directory / f"lock-{index}"
                directory.mkdir()
                store = ReviewStore(bundle(item()), directory)
                store.append(valid_annotation(store.item("item-1")))
                store.lock(reviewer_id="reviewer", attestation=reviewer_attestation())
                lock = read_json(store.lock_path)
                lock[field] = malformed
                try:
                    store.lock_path.write_bytes(canonical_bytes(lock))
                except UnicodeEncodeError:
                    store.lock_path.write_text(
                        json.dumps(lock, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                store.close()
                with self.assertRaisesRegex(BenchmarkError, message):
                    ReviewStore(bundle(item()), directory)

    def test_lock_reviewer_identity_rejects_padding_and_invalid_unicode(self):
        for index, reviewer_id in enumerate((" padded ", "\ud800", [], 7)):
            with self.subTest(reviewer_id=repr(reviewer_id)):
                directory = self.directory / f"identity-{index}"
                directory.mkdir()
                store = ReviewStore(bundle(item()), directory)
                store.append(valid_annotation(store.item("item-1")))
                with self.assertRaisesRegex(BenchmarkError, "reviewer_id"):
                    store.lock(reviewer_id=reviewer_id, attestation=reviewer_attestation())
                self.assertFalse(store.lock_path.exists())

    def test_every_persisted_json_artifact_bounds_integer_conversion(self):
        positive = b"9" * 5000
        negative = b"-" + b"9" * 5000

        bundle_path = self.directory / "oversized-bundle.json"
        bundle_path.write_bytes(
            canonical_bytes(bundle(item())).replace(
                b'"schema_version":1', b'"schema_version":' + positive,
            )
        )
        bundle_directory = self.directory / "bundle-artifacts"
        bundle_directory.mkdir()
        with self.assertRaisesRegex(BenchmarkError, "review bundle.*integer exceeds 4096 digits"):
            ReviewStore(bundle_path, bundle_directory)

        state_directory = self.directory / "state-artifacts"
        state_directory.mkdir()
        state_store = ReviewStore(bundle(item()), state_directory)
        state_store.state_path.write_bytes(
            state_store.state_path.read_bytes().replace(
                b'"last_sequence":0', b'"last_sequence":' + negative,
            )
        )
        state_store.close()
        with self.assertRaisesRegex(
            BenchmarkError, "annotation state.*integer exceeds 4096 digits",
        ):
            ReviewStore(bundle(item()), state_directory)

        event_directory = self.directory / "event-artifacts"
        event_directory.mkdir()
        event_store = ReviewStore(bundle(item()), event_directory)
        event_store.append(valid_annotation(event_store.item("item-1")))
        event_store.events_path.write_bytes(
            event_store.events_path.read_bytes().replace(
                b'"revision":1', b'"revision":' + positive,
            )
        )
        event_store.close()
        with self.assertRaisesRegex(
            BenchmarkError, "annotations line 1.*integer exceeds 4096 digits",
        ):
            ReviewStore(bundle(item()), event_directory)

        lock_directory = self.directory / "lock-artifacts"
        lock_directory.mkdir()
        lock_store = ReviewStore(bundle(item()), lock_directory)
        lock_store.append(valid_annotation(lock_store.item("item-1")))
        lock_store.lock(reviewer_id="reviewer", attestation=reviewer_attestation())
        lock_store.lock_path.write_bytes(
            lock_store.lock_path.read_bytes().replace(
                b'"schema_version":1', b'"schema_version":' + negative,
            )
        )
        lock_store.close()
        with self.assertRaisesRegex(
            BenchmarkError, "annotation lock.*integer exceeds 4096 digits",
        ):
            ReviewStore(bundle(item()), lock_directory)

    def test_every_persisted_json_artifact_rejects_depth_overflow(self):
        too_deep = json.loads(nested_json(257))

        deep_bundle = bundle(item())
        deep_bundle["items"][0]["source"] = too_deep
        bundle_path = self.directory / "deep-bundle.json"
        bundle_path.write_bytes(canonical_bytes(deep_bundle))
        bundle_directory = self.directory / "deep-bundle-artifacts"
        bundle_directory.mkdir()
        with self.assertRaisesRegex(
            BenchmarkError, "review bundle.*nesting exceeds 256 levels",
        ):
            ReviewStore(bundle_path, bundle_directory)

        state_directory = self.directory / "deep-state-artifacts"
        state_directory.mkdir()
        state_store = ReviewStore(bundle(item()), state_directory)
        deep_state = read_json(state_store.state_path)
        deep_state["latest"] = too_deep
        state_store.state_path.write_bytes(canonical_bytes(deep_state))
        state_store.close()
        with self.assertRaisesRegex(
            BenchmarkError, "annotation state.*nesting exceeds 256 levels",
        ):
            ReviewStore(bundle(item()), state_directory)

        event_directory = self.directory / "deep-event-artifacts"
        event_directory.mkdir()
        event_store = ReviewStore(bundle(item()), event_directory)
        event_store.append(valid_annotation(event_store.item("item-1")))
        deep_event = read_jsonl(event_store.events_path)[0]
        deep_event["note"] = too_deep
        event_store.events_path.write_bytes(canonical_bytes(deep_event))
        event_store.close()
        with self.assertRaisesRegex(
            BenchmarkError, "annotations line 1.*nesting exceeds 256 levels",
        ):
            ReviewStore(bundle(item()), event_directory)

        lock_directory = self.directory / "deep-lock-artifacts"
        lock_directory.mkdir()
        lock_store = ReviewStore(bundle(item()), lock_directory)
        lock_store.append(valid_annotation(lock_store.item("item-1")))
        lock_store.lock(reviewer_id="reviewer", attestation=reviewer_attestation())
        deep_lock = read_json(lock_store.lock_path)
        deep_lock["reviewer_id"] = too_deep
        lock_store.lock_path.write_bytes(canonical_bytes(deep_lock))
        lock_store.close()
        with self.assertRaisesRegex(
            BenchmarkError, "annotation lock.*nesting exceeds 256 levels",
        ):
            ReviewStore(bundle(item()), lock_directory)

    def test_complete_queue_locks_exact_bytes_and_disables_writes(self):
        review_bundle = bundle(item("item-1"), item("item-2", three_outputs=False))
        store = ReviewStore(review_bundle, self.directory)
        for review_item in store.bundle["items"]:
            store.append(valid_annotation(review_item))

        lock = store.lock(
            reviewer_id="pt-PT-reviewer", attestation=reviewer_attestation()
        )
        self.assertEqual(lock["reviewer_id"], "pt-PT-reviewer")
        self.assertEqual(lock["bundle_sha256"], hashlib.sha256(store.bundle_path.read_bytes()).hexdigest())
        self.assertEqual(lock["annotations_sha256"], hashlib.sha256(store.events_path.read_bytes()).hexdigest())
        self.assertEqual(lock["state_sha256"], hashlib.sha256(store.state_path.read_bytes()).hexdigest())
        with self.assertRaisesRegex(BenchmarkError, "locked"):
            store.append(valid_annotation(store.bundle["items"][0], revision=2))
        with self.assertRaisesRegex(BenchmarkError, "already locked"):
            store.lock(
                reviewer_id="other-reviewer", attestation=reviewer_attestation()
            )

        reopened = ReviewStore(review_bundle, self.directory)
        self.assertTrue(reopened.locked)
        self.assertEqual(reopened.lock_record, lock)

    def test_lock_before_complete_and_tampered_locked_artifacts_are_rejected(self):
        review_bundle = bundle(item())
        store = ReviewStore(review_bundle, self.directory)
        with self.assertRaisesRegex(BenchmarkError, "1 presentation remains"):
            store.lock(reviewer_id="reviewer", attestation=reviewer_attestation())
        store.append(valid_annotation(store.item("item-1")))
        store.lock(reviewer_id="reviewer", attestation=reviewer_attestation())
        store.events_path.write_bytes(store.events_path.read_bytes() + b" \n")
        with self.assertRaisesRegex(BenchmarkError, "annotations hash mismatch"):
            ReviewStore(review_bundle, self.directory)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_direct_directory_alias_and_artifact_symlink_are_refused(self):
        real_directory = self.directory / "real"
        real_directory.mkdir()
        alias = self.directory / "alias"
        alias.symlink_to(real_directory, target_is_directory=True)
        with self.assertRaisesRegex(BenchmarkError, "symlink annotation directory"):
            ReviewStore(bundle(item()), alias)

        target = self.directory / "outside.jsonl"
        events_link = real_directory / "annotations.jsonl"
        events_link.symlink_to(target)
        with self.assertRaisesRegex(BenchmarkError, "symlink artifact"):
            ReviewStore(bundle(item()), real_directory)


class ReviewHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ReviewStore(bundle(item(three_outputs=False)), Path(self.temporary.name))
        self.server = create_server(self.store, host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(self, method: str, path: str, body: bytes | None = None, **headers: str):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        return response.status, response_headers, payload

    def raw_request(
        self,
        method: str,
        target: str,
        headers: list[tuple[str, str]],
        body: bytes = b"",
    ):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
        for name, value in headers:
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        payload = response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        return response.status, response_headers, payload

    def test_state_and_static_allowlist_have_restrictive_csp(self):
        status, headers, payload = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Security-Policy"], CSP)
        self.assertEqual(json.loads(payload)["bundle"]["items"][0]["id"], "item-1")

        status, headers, payload = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Security-Policy"], CSP)
        self.assertIn(b'<main id="review-workspace"', payload)

        for path in ("/../bundle.json", "/%2e%2e/bundle.json", "/app.js/extra", "/app.js?debug=1"):
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 404, path)

    def test_post_rejects_wrong_origin_and_oversize_before_reading(self):
        payload = canonical_bytes(valid_annotation(self.store.item("item-1")))
        status, _, response = self.request(
            "POST", "/api/annotations", payload,
            Origin="http://example.test", **{"Content-Type": "application/json"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(
            response,
            b'{"error":{"code":"origin_mismatch","message":"request origin does not match this review server"}}\n',
        )

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.putrequest("POST", "/api/annotations")
        connection.putheader("Origin", self.origin)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(MAX_BODY_BYTES + 1))
        connection.endheaders()
        response = connection.getresponse()
        body = response.read()
        connection.close()
        self.assertEqual(response.status, 413)
        self.assertEqual(json.loads(body)["error"]["code"], "body_too_large")
        self.assertFalse(self.store.events_path.exists())

    def test_annotation_and_lock_routes_use_real_store_contract(self):
        annotation = canonical_bytes(valid_annotation(self.store.item("item-1")))
        status, _, payload = self.request(
            "POST", "/api/annotations", annotation,
            Origin=self.origin, **{"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(payload)["annotation"]["revision"], 1)

        lock_payload = canonical_bytes({
            "reviewer_id": "pt-PT-reviewer",
            "attestation": reviewer_attestation(),
        })
        status, _, payload = self.request(
            "POST", "/api/lock", lock_payload,
            Origin=self.origin, **{"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(payload)["lock"]["reviewer_id"], "pt-PT-reviewer")

    def test_malformed_annotation_containers_return_canonical_400_and_keep_serving(self):
        malformed = valid_annotation(self.store.item("item-1"))
        malformed["confidence"] = []
        body = canonical_bytes(malformed)
        status, headers, payload = self.request(
            "POST", "/api/annotations", body,
            Origin=self.origin, **{"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(headers["Content-Security-Policy"], CSP)
        self.assertEqual(json.loads(payload)["error"]["code"], "validation_error")
        self.assertEqual(payload, canonical_bytes(json.loads(payload)))
        self.assertFalse(self.store.events_path.exists())

        status, headers, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Security-Policy"], CSP)

    def test_untrusted_json_resource_limits_return_canonical_400_and_keep_serving(self):
        annotation = canonical_bytes(valid_annotation(self.store.item("item-1")))
        requests = (
            (annotation.replace(b'"revision":1', b'"revision":' + b"9" * 5000), "invalid_json"),
            (annotation.replace(b'"revision":1', b'"revision":-' + b"9" * 5000), "invalid_json"),
            (b'{"payload":' + nested_json(256) + b"}", "invalid_json"),
            (b'{"payload":' + nested_json(2000) + b"}", "invalid_json"),
            (b'{"confidence":"high","confidence":"low"}', "invalid_json"),
            (b'\xff', "invalid_json"),
        )
        for body, code in requests:
            with self.subTest(size=len(body), prefix=body[:24]):
                status, headers, payload = self.request(
                    "POST", "/api/annotations", body,
                    Origin=self.origin, **{"Content-Type": "application/json"},
                )
                self.assertEqual(status, 400)
                self.assertEqual(headers["Content-Security-Policy"], CSP)
                self.assertEqual(json.loads(payload)["error"]["code"], code)
                self.assertEqual(payload, canonical_bytes(json.loads(payload)))

                status, headers, _ = self.request("GET", "/api/state")
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Security-Policy"], CSP)

        boundary_annotation = annotation.replace(
            b'"revision":1', b'"revision":' + b"7" * 4096,
        )
        status, headers, payload = self.request(
            "POST", "/api/annotations", boundary_annotation,
            Origin=self.origin, **{"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(headers["Content-Security-Policy"], CSP)
        self.assertEqual(json.loads(payload)["error"]["code"], "validation_error")
        self.assertFalse(self.store.events_path.exists())

        boundary_depth = b'{"payload":' + nested_json(255) + b"}"
        status, headers, payload = self.request(
            "POST", "/api/annotations", boundary_depth,
            Origin=self.origin, **{"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(headers["Content-Security-Policy"], CSP)
        self.assertEqual(json.loads(payload)["error"]["code"], "validation_error")
        self.assertFalse(self.store.events_path.exists())

        status, headers, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Security-Policy"], CSP)

    def test_http_rejects_non_origin_targets_userinfo_hosts_and_non_digit_lengths(self):
        annotation = canonical_bytes(valid_annotation(self.store.item("item-1")))
        bad_host = f"user@127.0.0.1:{self.port}"
        status, headers, payload = self.raw_request(
            "POST",
            "/api/annotations",
            [
                ("Host", bad_host),
                ("Origin", f"http://{bad_host}"),
                ("Content-Type", "application/json"),
                ("Content-Length", f"+{len(annotation)}"),
            ],
            annotation,
        )
        self.assertEqual(status, 403)
        self.assertEqual(headers["Content-Security-Policy"], CSP)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_host")
        self.assertFalse(self.store.events_path.exists())

        status, headers, payload = self.raw_request(
            "GET",
            f"http://example.test:{self.port}/api/state",
            [("Host", f"127.0.0.1:{self.port}")],
        )
        self.assertEqual(status, 400)
        self.assertEqual(headers["Content-Security-Policy"], CSP)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_target")

    def test_duplicate_headers_and_arbitrary_methods_use_canonical_security_errors(self):
        annotation = canonical_bytes(valid_annotation(self.store.item("item-1")))
        status, headers, payload = self.raw_request(
            "POST",
            "/api/annotations",
            [
                ("Host", f"127.0.0.1:{self.port}"),
                ("Origin", self.origin),
                ("Content-Type", "application/json"),
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(annotation))),
            ],
            annotation,
        )
        self.assertEqual(status, 400)
        self.assertEqual(headers["Content-Security-Policy"], CSP)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_headers")
        self.assertFalse(self.store.events_path.exists())

        status, headers, payload = self.raw_request(
            "POST",
            "/api/annotations",
            [
                ("Host", f"127.0.0.1:{self.port}"),
                ("Origin", self.origin),
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(annotation))),
                ("Content-Length", str(len(annotation) + 1)),
            ],
            annotation,
        )
        self.assertEqual(status, 400)
        self.assertEqual(headers["Content-Security-Policy"], CSP)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_headers")

        status, headers, payload = self.raw_request(
            "POST",
            "/api/annotations",
            [
                ("Host", f"127.0.0.1:{self.port}"),
                ("Origin", self.origin),
                ("Content-Type", "application/json"),
                ("Content-Length", "9" * 5000),
            ],
        )
        self.assertEqual(status, 413)
        self.assertEqual(headers["Content-Security-Policy"], CSP)
        self.assertEqual(json.loads(payload)["error"]["code"], "body_too_large")

        status, headers, payload = self.raw_request(
            "BREW",
            "/api/state",
            [("Host", f"127.0.0.1:{self.port}")],
        )
        self.assertEqual(status, 405)
        self.assertEqual(headers["Content-Security-Policy"], CSP)
        self.assertEqual(json.loads(payload)["error"]["code"], "method_not_allowed")
        self.assertEqual(payload, canonical_bytes(json.loads(payload)))


class ReviewUiBehaviorTests(unittest.TestCase):
    def test_node_behavior_suite_executes_the_shipped_javascript(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for the offline reviewer behavior suite")
        repository = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [node, "tests/test_benchmark_review_ui.js"],
            cwd=repository,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("review UI behavior: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
