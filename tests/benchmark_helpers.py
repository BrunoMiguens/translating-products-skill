from __future__ import annotations

from pathlib import Path


SURFACES = ("ui-mobile", "web", "marketing", "app-store", "documentation")
DIFFICULTIES = ("simple", "contextual", "adversarial")


def synthetic_balanced_cases() -> tuple[list[dict], dict]:
    """Return a complete, hand-shaped in-memory cohort for schema tests."""
    cases: list[dict] = []
    seeded_errors: dict[str, list[dict]] = {}
    diagnostic_index = 0
    for surface_index, surface in enumerate(SURFACES):
        for task, difficulty_counts in (
            ("translation", (3, 3, 2)),
            ("review", (1, 1, 2)),
        ):
            sequence = 0
            for difficulty, count in zip(DIFFICULTIES, difficulty_counts):
                for _ in range(count):
                    sequence += 1
                    case_id = f"{surface}-{task}-{sequence:02d}"
                    diagnostic = diagnostic_index < 15
                    diagnostic_index += 1
                    case = {
                        "id": case_id,
                        "dataset_version": "pt-pt-v1",
                        "task": task,
                        "surface": surface,
                        "content_type": "product-copy",
                        "difficulty": difficulty,
                        "diagnostic": diagnostic,
                        "source_locale": "en-GB",
                        "target_locale": "pt-PT",
                        "source": f"Source {case_id}",
                        "context": "Product context.",
                        "audience": "Adults in Portugal",
                        "register": "informal",
                        "constraints": {"output": "text"},
                        "glossary": [],
                        "protected_terms": ["Lume"],
                        "invariants": [],
                        "reference": f"Referência {case_id}",
                        "reference_notes": "Human review required.",
                        "automatic_checks": [],
                    }
                    if task == "review":
                        case["candidate"] = f"Candidato {case_id}"
                        seeded_errors[case_id] = [{
                            "id": f"{case_id}-e1",
                            "dimension": "locale-audience",
                            "severity": "major",
                            "candidate_span": "Candidato",
                            "accepted_corrections": ["Correção"],
                            "correction_required": True,
                            "notes": "Reviewer decision.",
                        }]
                    cases.append(case)
    return cases, seeded_errors


def write_synthetic_dataset(directory: Path) -> Path:
    from scripts.benchmark.common import append_jsonl_fsync, atomic_write_json, sha256_file

    dataset = directory / "pt-pt-v1"
    dataset.mkdir()
    cases, seeded_errors = synthetic_balanced_cases()
    for case in cases:
        append_jsonl_fsync(dataset / "cases.jsonl", case)
    atomic_write_json(dataset / "seeded-errors.json", seeded_errors)
    (dataset / "rubric.md").write_text("Rubric\n", encoding="utf-8")
    context = dataset / "project-context"
    context.mkdir()
    required = {
        "project-brief.md": "Status: approved\n- Name: Synthetic fixture\n",
        "locales.yaml": "source_locale: en-GB\ntarget_locales: [pt-PT]\n",
        "glossary.csv": "source_term,target_term,locale,context,status,notes\nfile,ficheiro,pt-PT,test,approved,fixture\n",
        "style-guide.md": "Status: approved\n- Voice: Fixture\n",
        "protected-terms.txt": "Lume\n",
    }
    for name, content in required.items():
        (context / name).write_text(content, encoding="utf-8")
    atomic_write_json(context / "setup-approval.json", {
        "status": "approved",
        "approved_by": "synthetic-fixture",
        "approved_at": "2026-08-03T10:00:00Z",
        "context_sha256": {name: sha256_file(context / name) for name in required},
        "approved_empty": [],
    })
    (context / "decisions.md").write_text("Synthetic decisions.\n", encoding="utf-8")
    (context / "translation-memory.csv").write_text(
        "source_hash,source_text,target_text,source_locale,target_locale,context,status,provenance\n",
        encoding="utf-8",
    )
    (context / "research-sources.md").write_text("No research.\n", encoding="utf-8")
    return dataset


def write_reviewer_signoff(dataset: Path, *, reviewer: str, approved_at: str) -> None:
    from scripts.benchmark.common import atomic_write_json, canonical_bytes, sha256_bytes
    from scripts.benchmark.prepare import build_curation_packet

    packet = build_curation_packet(dataset)
    atomic_write_json(dataset / "reference-signoff.json", {
        "schema_version": 1,
        "dataset_version": "pt-pt-v1",
        "reviewer_id": reviewer,
        "approved_at": approved_at,
        "human_reference_authored": True,
        "approved_case_ids": packet["case_ids"],
        "dataset_sha256": packet["dataset_sha256"],
        "context_sha256": packet["context_sha256"],
        "curation_packet_sha256": sha256_bytes(canonical_bytes(packet)),
    })
