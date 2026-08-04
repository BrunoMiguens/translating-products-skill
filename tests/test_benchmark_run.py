from __future__ import annotations

import json
import hashlib
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.benchmark.common import (
    BenchmarkError,
    atomic_write_json,
    canonical_bytes,
    read_jsonl,
    sha256_bytes,
)
from scripts.benchmark.run import (
    CliRunner,
    FakeRunner,
    Invocation,
    RunResult,
    RunSpec,
    build_schedule,
    classify_failure,
    execute_schedule,
    export_manual_packages,
    import_manual_responses,
    validate_runner_config,
)
from tests.benchmark_helpers import synthetic_balanced_cases
from tests.test_benchmark_prompts import one_translation_case, templates


def fake_config(**overrides: object) -> dict:
    config = {
        "schema_version": 1,
        "agent": "deterministic-fake-agent",
        "host_version": "1.0.0",
        "model": "fixture-model-v1",
        "mode": "fake",
        "command": [],
        "settings": {"temperature": 0, "research": "case-declared-only"},
        "timeout_seconds": 120,
        "suite_path": ".",
        "scratch_root": ".benchmark-tmp",
    }
    config.update(overrides)
    return config


class InspectingRunner(FakeRunner):
    def __init__(self, output: str = "same output"):
        super().__init__(output)
        self.snapshots: list[set[str]] = []
        self.project_dirs: list[Path] = []

    def invoke(self, *, prompt: str, project_dir: Path, timeout_seconds: int):
        self.project_dirs.append(project_dir)
        self.snapshots.append({path.relative_to(project_dir).as_posix() for path in project_dir.rglob("*")})
        return super().invoke(prompt=prompt, project_dir=project_dir, timeout_seconds=timeout_seconds)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp.name)
        self.suite = self.temp_dir / "suite"
        (self.suite / "skills" / "translating-products").mkdir(parents=True)
        (self.suite / "skills" / "translating-products" / "SKILL.md").write_text(
            "approved suite\n", encoding="utf-8"
        )
        translation = self.suite / ".translation"
        translation.mkdir()
        context = {
            "project-brief.md": "Status: approved\n- Name: Fixture\n",
            "locales.yaml": "source_locale: en-US\ntarget_locales: [pt-PT]\n",
            "glossary.csv": "source_term,target_term,locale,context,status,notes\n",
            "style-guide.md": "Status: approved\n- Voice: Clear\n",
            "protected-terms.txt": "Codex\n",
        }
        for name, content in context.items():
            (translation / name).write_text(content, encoding="utf-8")
        atomic_write_json(translation / "setup-approval.json", {
            "status": "approved",
            "approved_by": "fixture-owner",
            "approved_at": "2026-08-03T10:00:00Z",
            "context_sha256": {
                name: hashlib.sha256((translation / name).read_bytes()).hexdigest()
                for name in context
            },
            "approved_empty": ["glossary.csv"],
        })
        self.scratch = self.temp_dir / "scratch"
        self.evidence = self.temp_dir / "evidence"

    def tearDown(self):
        self.temp.cleanup()

    def config(self, **overrides: object) -> dict:
        return fake_config(**{
            "suite_path": str(self.suite),
            "scratch_root": str(self.scratch),
            **overrides,
        })

    def balanced_cases(self) -> list[dict]:
        cases, _ = synthetic_balanced_cases()
        return [
            {
                **case,
                "automatic_checks": [f"private-check-{case['id']}"],
                "reference_notes": f"private-notes-{case['id']}",
            }
            for case in cases
        ]

    def write_adapter(self) -> Path:
        adapter = self.temp_dir / "sandbox_adapter.py"
        adapter.write_text(
            r'''import hashlib, json, os, subprocess, sys
from pathlib import Path

op = sys.argv[1]
def arg(name): return sys.argv[sys.argv.index(name) + 1]
project = Path(arg('--project')).resolve()
home = Path(arg('--home')).resolve()
policy_path = Path(arg('--policy')).resolve()
policy_hash = hashlib.sha256(policy_path.read_bytes()).hexdigest()
(Path(__file__).with_suffix('.log')).open('a', encoding='utf-8').write(op + '\n')

if op == 'probe':
    # Deliberately old self-attestation path: the fixed harness must never use it.
    result = {
      'schema_version': 1, 'outside_read_denied': True, 'suite_read_denied': True,
      'context_read_denied': True, 'home': str(home), 'home_isolated': True,
      'policy_sha256': policy_hash, 'network_policy_enforced': True,
      'tool_policy_enforced': True, 'research_policy_enforced': True}
    print(json.dumps(result, sort_keys=True)); raise SystemExit(0)

split = sys.argv.index('--')
agent = sys.argv[split + 1:]
outcome = next((x.split('=', 1)[1] for x in agent if x.startswith('--adapter-outcome=')), '')
agent = [x for x in agent if not x.startswith('--adapter-outcome=')]

site = home / 'sitecustomize.py'
site.write_text("""import os, sys
from pathlib import Path
project = Path(os.environ['SANDBOX_PROJECT']).resolve()
allowed = {Path(value).resolve() for value in os.environ.get('SANDBOX_ALLOWED_FILES', '').split(os.pathsep) if value}
stdlib = Path(sys.base_prefix).resolve()
home = Path(os.environ['HOME']).resolve()
def inside(path, root):
    try: path.relative_to(root); return True
    except ValueError: return False
def audit(event, args):
    if event == 'open' and args and isinstance(args[0], (str, bytes)):
        path = Path(args[0]).resolve()
        if path not in allowed and not inside(path, project) and not inside(path, stdlib) and not inside(path, home):
            raise PermissionError(path)
    if event in ('socket.connect', 'subprocess.Popen'):
        raise PermissionError(event)
sys.addaudithook(audit)
""", encoding='utf-8')
environment = {
    **os.environ,
    'HOME': str(home),
    'PYTHONPATH': str(home),
    'SANDBOX_PROJECT': str(project),
    'SANDBOX_ALLOWED_FILES': os.pathsep.join([str(Path(agent[1]).resolve())] if len(agent) > 1 else []),
}
started = '2026-08-03T00:00:00Z'
completed = subprocess.run(agent, input=sys.stdin.read(), text=True, capture_output=True, cwd=project, env=environment)
if outcome == 'invalid-envelope': print('{}'); raise SystemExit(0)
envelope = {
 'schema_version': 1, 'process_started': True, 'exit_code': completed.returncode,
 'started_at': started, 'completed_at': started, 'stdout': completed.stdout,
 'stderr': completed.stderr, 'timed_out': False, 'refused': outcome == 'refused',
 'malformed_output': outcome == 'malformed', 'tool_misuse': outcome == 'tool-misuse',
 'reason': outcome or None, 'usage': {'input_tokens': 1, 'output_tokens': 1},
 'telemetry': {'policy_sha256': policy_hash},
 'applied_policy_sha256': ('f' * 64 if outcome == 'policy-mismatch' else policy_hash)}
print(json.dumps(envelope, sort_keys=True))
''',
            encoding="utf-8",
        )
        return adapter

    def cli_config(self, **overrides: object) -> dict:
        adapter = self.write_adapter()
        agent = self.temp_dir / "agent.py"
        agent.write_text(
            "import json, os, sys\n"
            "print(json.dumps({'stdin': sys.stdin.read(), 'cwd': os.getcwd(), 'home': os.environ['HOME']}))\n",
            encoding="utf-8",
        )
        values = {
            "mode": "cli",
            "command": [sys.executable, str(agent)],
            "sandbox_adapter": [sys.executable, str(adapter)],
            "settings": {
                "temperature": 0,
                "tools": ["read-project"],
                "network": "disabled",
                "research": "case-declared-only",
            },
            **overrides,
        }
        return self.config(**values)

    def test_schedule_has_405_unique_randomized_runs(self):
        """Break: wrong attempt/diagnostic expansion would invalidate the balanced experiment."""
        cases = self.balanced_cases()

        schedule = build_schedule(cases, self.config(), 20260803)

        self.assertEqual(len(schedule), 405)
        self.assertEqual(len({spec.run_id for spec in schedule}), 405)
        self.assertEqual(sum(spec.condition == "context_only" for spec in schedule), 45)
        self.assertEqual(schedule, build_schedule(cases, self.config(), 20260803))
        self.assertNotEqual(schedule, build_schedule(cases, self.config(), 20260805))
        self.assertEqual({spec.attempt for spec in schedule}, {1, 2, 3})

    def test_each_condition_has_the_required_isolation_boundary(self):
        """Break: controls could receive suite files or suite runs could lose approved context."""
        case = one_translation_case()
        runner = InspectingRunner()
        schedule = [
            RunSpec(f"run-{condition}", case["id"], condition, 1)
            for condition in ("normal", "suite", "context_only")
        ]

        execute_schedule(
            schedule,
            runner,
            self.evidence,
            cases=[case],
            config=self.config(),
            templates=templates(),
        )

        normal, suite, context_only = runner.snapshots
        self.assertNotIn("skills", normal)
        self.assertNotIn(".translation", normal)
        self.assertIn("skills/translating-products/SKILL.md", suite)
        self.assertIn(".translation/setup-approval.json", suite)
        self.assertNotIn("skills", context_only)
        self.assertNotIn(".translation", context_only)
        self.assertTrue(all("task.txt" in snapshot for snapshot in runner.snapshots))
        self.assertIn("<project-context>", runner.prompts[2])
        self.assertTrue(all(not project.exists() for project in runner.project_dirs))

    def test_cli_uses_argv_stdin_safe_cwd_and_no_shell_interpolation(self):
        """Break: treating adversarial prompts as shell source could execute arbitrary commands."""
        sentinel = self.temp_dir / "benchmark-owned"
        config = self.cli_config()
        runner = CliRunner(config["command"], sandbox_adapter=config["sandbox_adapter"])
        policy = self.temp_dir / "policy.json"
        atomic_write_json(policy, {"tools": [], "network": "disabled", "research": "disabled"})
        home = self.temp_dir / "home"
        home.mkdir()
        prompt = f"$(touch {sentinel})\n`uname`"

        result = runner.invoke(
            prompt=prompt, project_dir=self.temp_dir, home_dir=home,
            policy_path=policy, timeout_seconds=10,
        )

        payload = json.loads(result.stdout)
        self.assertFalse(sentinel.exists())
        self.assertEqual(payload["stdin"], prompt)
        self.assertEqual(Path(payload["cwd"]).resolve(), self.temp_dir.resolve())
        self.assertEqual(Path(payload["home"]).resolve(), home.resolve())
        self.assertEqual(result.argv[0], sys.executable)
        self.assertFalse(result.shell)

    def test_cli_requires_adapter_and_probe_proves_isolation_before_generation(self):
        """Break: a CLI agent could run with host filesystem/HOME access or unenforced policy."""
        with self.assertRaisesRegex(BenchmarkError, "manual mode"):
            validate_runner_config(self.config(mode="cli", command=["agent"]))
        cases = self.balanced_cases()
        schedule = build_schedule(cases, self.cli_config(), 20260803)
        first_case_id = cases[0]["id"]
        primary_specs = [
            spec for spec in schedule
            if spec.case_id == first_case_id
            and spec.condition in ("normal", "suite")
            and spec.attempt == 1
        ]

        results = execute_schedule(
            primary_specs,
            CliRunner(
                self.cli_config()["command"],
                sandbox_adapter=self.cli_config()["sandbox_adapter"],
            ),
            self.evidence,
            cases=cases,
            config=self.cli_config(),
            templates=templates(),
        )

        self.assertEqual(len(results), 2)
        probes = list((self.evidence / "probes").glob("*.json"))
        self.assertEqual(len(probes), 1)
        probe = json.loads(probes[0].read_text(encoding="utf-8"))
        self.assertEqual(probe["observations"]["outside_read"], "denied")
        self.assertEqual(probe["observations"]["suite_read"], "denied")
        self.assertEqual(probe["observations"]["context_read"], "denied")
        self.assertEqual(probe["observations"]["network_attempt"], "denied")
        self.assertEqual(probe["observations"]["tool_attempt"], "denied")
        self.assertEqual(probe["observations"]["research_attempt"], "denied")
        probe_index = read_jsonl(self.evidence / "probe-runs.jsonl")
        self.assertEqual(
            probe_index[0]["record_sha256"], hashlib.sha256(probes[0].read_bytes()).hexdigest()
        )
        manifest = json.loads((self.evidence / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["sandbox_probe"]["probe_version"], 1)
        self.assertEqual(manifest["sandbox_probe"]["snapshot_sha256"], manifest["input_snapshot"]["sha256"])
        adapter_log = self.write_adapter().with_suffix(".log").read_text(encoding="utf-8")
        self.assertNotIn("probe\n", adapter_log)
        execute_schedule(
            primary_specs,
            CliRunner(self.cli_config()["command"], sandbox_adapter=self.cli_config()["sandbox_adapter"]),
            self.evidence, cases=cases, config=self.cli_config(), templates=templates(),
        )
        self.assertEqual(len(list((self.evidence / "probes").glob("*.json"))), 2)
        mismatched_runner = CliRunner(
            [sys.executable, "different-agent.py"],
            sandbox_adapter=self.cli_config()["sandbox_adapter"],
        )
        with self.assertRaisesRegex(BenchmarkError, "configured"):
            execute_schedule(
                primary_specs, mismatched_runner, self.evidence / "mismatch",
                cases=cases, config=self.cli_config(), templates=templates(),
            )

        outside = self.temp_dir / "outside-secret.txt"
        outside.write_text("host secret\n", encoding="utf-8")
        reader = self.temp_dir / "reader.py"
        reader.write_text(
            "import sys\nfrom pathlib import Path\n"
            "try: Path(sys.argv[1]).read_text(); print('READ')\n"
            "except PermissionError: print('DENIED')\n",
            encoding="utf-8",
        )
        denied_config = self.cli_config(command=[sys.executable, str(reader), str(outside)])
        denied_schedule = build_schedule(cases, denied_config, 20260803)
        denied_result = execute_schedule(
            denied_schedule[:1],
            CliRunner(denied_config["command"], sandbox_adapter=denied_config["sandbox_adapter"]),
            self.evidence / "outside-denied", cases=cases,
            config=denied_config, templates=templates(),
        )[0]
        self.assertEqual(
            (self.evidence / "outside-denied" / denied_result.raw_output_path).read_text(encoding="utf-8"),
            "DENIED\n",
        )

    def test_self_attesting_adapter_and_preseeded_probe_are_rejected(self):
        """Break: adapter-authored booleans or cached junk could bypass a harness-owned probe."""
        cases = self.balanced_cases()
        lying = self.temp_dir / "lying_adapter.py"
        lying.write_text(
            "import hashlib,json,sys\nfrom pathlib import Path\n"
            "def arg(n): return sys.argv[sys.argv.index(n)+1]\n"
            "p=Path(arg('--policy')); h=hashlib.sha256(p.read_bytes()).hexdigest()\n"
            "print(json.dumps({'schema_version':1,'outside_read_denied':True,"
            "'suite_read_denied':True,'context_read_denied':True,'home':arg('--home'),"
            "'home_isolated':True,'policy_sha256':h,'network_policy_enforced':True,"
            "'tool_policy_enforced':True,'research_policy_enforced':True}))\n",
            encoding="utf-8",
        )
        lying_config = self.cli_config(sandbox_adapter=[sys.executable, str(lying)])
        schedule = build_schedule(cases, lying_config, 20260803)
        with self.assertRaisesRegex(BenchmarkError, "probe"):
            execute_schedule(
                schedule[:1],
                CliRunner(lying_config["command"], sandbox_adapter=lying_config["sandbox_adapter"]),
                self.evidence / "lying", cases=cases,
                config=lying_config, templates=templates(),
            )

        poisoned = self.evidence / "poisoned"
        (poisoned / "probes").mkdir(parents=True)
        policy = {
            "schema_version": 1,
            "tools": ["read-project"],
            "network": "disabled",
            "research": {"mode": "case-declared-only", "unresolved_question": None},
        }
        digest = sha256_bytes(canonical_bytes(policy))
        atomic_write_json(poisoned / "probes" / f"{digest}.json", {})
        valid = self.cli_config()
        with self.assertRaises(BenchmarkError):
            execute_schedule(
                schedule[:1], CliRunner(valid["command"], sandbox_adapter=valid["sandbox_adapter"]),
                poisoned, cases=cases, config=valid, templates=templates(),
            )

    def test_cli_refuses_scratch_inside_repository_and_failed_probe(self):
        """Break: an in-repository scratch root or unproved adapter could expose host files."""
        config = self.cli_config(scratch_root=str(Path(__file__).resolve().parents[1] / ".tmp"))
        cases = self.balanced_cases()
        schedule = build_schedule(cases, config, 20260803)
        with self.assertRaisesRegex(BenchmarkError, "outside"):
            execute_schedule(
                schedule[:1], CliRunner(config["command"], sandbox_adapter=config["sandbox_adapter"]),
                self.evidence, cases=cases, config=config, templates=templates(),
            )

        bad_adapter = self.temp_dir / "bad_adapter.py"
        bad_adapter.write_text("print('{}')\n", encoding="utf-8")
        bad = self.cli_config(sandbox_adapter=[sys.executable, str(bad_adapter)])
        with self.assertRaisesRegex(BenchmarkError, "probe"):
            execute_schedule(
                schedule[:1], CliRunner(bad["command"], sandbox_adapter=bad["sandbox_adapter"]),
                self.evidence / "bad", cases=cases, config=bad, templates=templates(),
            )

    def test_each_attempt_is_fresh_and_raw_output_is_content_addressed_once(self):
        """Break: project reuse or per-run raw copies could contaminate evidence and waste storage."""
        case = one_translation_case()
        runner = InspectingRunner("same output")
        schedule = [RunSpec(f"run-{attempt}", case["id"], "normal", attempt) for attempt in range(1, 4)]

        results = execute_schedule(
            schedule,
            runner,
            self.evidence,
            cases=[case],
            config=self.config(),
            templates=templates(),
        )

        self.assertEqual(len({result.project_fingerprint for result in results}), 3)
        self.assertEqual(len(list((self.evidence / "raw").glob("*.txt"))), 1)
        self.assertEqual(len({result.output_sha256 for result in results}), 1)
        self.assertEqual(len(read_jsonl(self.evidence / "runs.jsonl")), 3)

    def test_only_prestart_infrastructure_failures_are_retryable(self):
        """Break: rerunning started model failures could bias the benchmark sample."""
        self.assertEqual(
            classify_failure({"process_started": False, "reason": "ENOENT"}),
            "infrastructure",
        )
        for record in (
            {"process_started": True, "timed_out": True},
            {"process_started": True, "exit_code": 1},
            {"process_started": True, "refused": True},
            {"process_started": True, "malformed_output": True},
            {"process_started": True, "tool_misuse": True},
        ):
            self.assertEqual(classify_failure(record), "model_outcome")

    def test_outputs_and_telemetry_are_redacted_before_any_persistence(self):
        """Break: configured literal secrets could be stored in raw output or run telemetry."""
        secret = "token-super-secret"
        case = one_translation_case()
        class SecretRunner(FakeRunner):
            def invoke(inner_self, *, prompt: str, project_dir: Path, timeout_seconds: int):
                now = secret
                return Invocation(
                    (secret,), False, True, now, now, 1, secret, f"stderr {secret}",
                    reason=secret, telemetry={secret: {"trace": secret}},
                    usage={secret: secret}, applied_policy_sha256=secret,
                )
        runner = SecretRunner()
        os.environ["BENCHMARK_TEST_SECRET"] = secret
        self.addCleanup(os.environ.pop, "BENCHMARK_TEST_SECRET", None)

        results = execute_schedule(
            [RunSpec("redacted-run", case["id"], "normal", 1)],
            runner,
            self.evidence,
            cases=[case],
            config=self.config(secret_env=["BENCHMARK_TEST_SECRET"]),
            templates=templates(),
        )

        persisted = b"".join(path.read_bytes() for path in self.evidence.rglob("*.*"))
        self.assertNotIn(secret.encode(), persisted)
        self.assertIn(b"[REDACTED]", persisted)
        self.assertTrue(results[0].redacted)
        records = (self.evidence / "runs.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(secret, records)

    def test_schema_v1_legacy_record_defaults_only_optional_usage(self):
        """Break: adding usage to schema v1 could make valid prior evidence impossible to resume."""
        legacy = {
            "schema_version": 1,
            "run_id": "legacy-run", "case_id": "legacy-case", "condition": "normal",
            "attempt": 1, "runner_mode": "fake", "status": "completed",
            "failure_class": "success", "process_started": True,
            "started_at": "2026-08-03T00:00:00Z", "completed_at": "2026-08-03T00:00:01Z",
            "exit_code": 0, "timed_out": False, "refused": False,
            "malformed_output": False, "tool_misuse": False, "reason": None,
            "output_sha256": "0" * 64, "raw_output_path": "raw/legacy.txt",
            "stderr": "", "telemetry": {}, "redacted": False,
            "project_fingerprint": "legacy-project", "argv": ["fake"], "shell": False,
        }

        result = RunResult.from_record(legacy)

        self.assertEqual(result.usage, {})
        missing_schema = dict(legacy)
        missing_schema.pop("schema_version")
        with self.assertRaisesRegex(BenchmarkError, "schema version"):
            RunResult.from_record(missing_schema)

    def test_config_rejects_shell_commands_and_primary_policy_differences(self):
        """Break: shell strings or condition-specific policies would weaken safety/comparability."""
        with self.assertRaisesRegex(BenchmarkError, "array"):
            validate_runner_config(self.config(mode="cli", command="agent --run"))
        with self.assertRaisesRegex(BenchmarkError, "array"):
            validate_runner_config(
                self.config(mode="cli", command=["agent"], sandbox_adapter="sandbox --")
            )
        with self.assertRaisesRegex(BenchmarkError, "primary condition"):
            validate_runner_config(
                self.config(
                    condition_settings={
                        "normal": {"tools": ["read"]},
                        "suite": {"tools": ["read", "web"]},
                    }
                )
            )
        with self.assertRaisesRegex(BenchmarkError, "case-declared-only"):
            validate_runner_config(
                self.config(settings={"temperature": 0, "research": "always"})
            )
        with self.assertRaisesRegex(BenchmarkError, "mode"):
            validate_runner_config(self.config(mode=["fake"]))
        with self.assertRaisesRegex(BenchmarkError, "primary condition"):
            validate_runner_config(
                self.config(
                    condition_settings={
                        "normal": {"temperature": 0},
                        "suite": {"temperature": 1},
                    }
                )
            )

    def test_schedule_rejects_malformed_cohorts_before_randomization(self):
        """Break: malformed counts/types could produce a superficially plausible schedule."""
        cases = self.balanced_cases()
        invalid_cohorts = [
            cases[:-1],
            [*cases[:-1], {**cases[-1], "id": "replacement", "task": "translation"}],
            [{**case, "diagnostic": 1} if index == 0 else case for index, case in enumerate(cases)],
            [{**case, "surface": "web"} if index == 0 else case for index, case in enumerate(cases)],
        ]
        for cohort in invalid_cohorts:
            with self.subTest(cohort=len(cohort)), self.assertRaises(BenchmarkError):
                build_schedule(cohort, self.config(), 20260803)

    def test_cli_envelope_flags_drive_real_model_outcome_classification(self):
        """Break: exit-zero refusals/malformed/tool misuse could be recorded as success."""
        cases = self.balanced_cases()
        for index, (flag, field) in enumerate((
            ("refused", "refused"),
            ("malformed", "malformed_output"),
            ("tool-misuse", "tool_misuse"),
            ("invalid-envelope", "malformed_output"),
            ("policy-mismatch", "malformed_output"),
        )):
            config = self.cli_config()
            config["command"] = [*config["command"], f"--adapter-outcome={flag}"]
            schedule = build_schedule(cases, config, 20260803)
            result = execute_schedule(
                schedule[:1], CliRunner(config["command"], sandbox_adapter=config["sandbox_adapter"]),
                self.evidence / f"outcome-{index}", cases=cases, config=config, templates=templates(),
            )[0]
            self.assertTrue(getattr(result, field))
            self.assertEqual(result.failure_class, "model_outcome")

    def test_suite_attempts_use_one_frozen_signed_snapshot(self):
        """Break: source mutation between attempts could change the treatment being measured."""
        cases = self.balanced_cases()
        suite_specs = [
            spec for spec in build_schedule(cases, self.config(), 20260803)
            if spec.condition == "suite"
        ][:2]
        (self.suite / ".translation/unapproved-draft.md").write_text(
            "must not enter treatment\n", encoding="utf-8"
        )

        class MutatingRunner(InspectingRunner):
            def invoke(inner_self, *, prompt: str, project_dir: Path, timeout_seconds: int):
                content = (project_dir / "skills/translating-products/SKILL.md").read_text(encoding="utf-8")
                if (project_dir / ".translation/unapproved-draft.md").exists():
                    raise AssertionError("unsigned context entered the suite project")
                inner_self.snapshots.append({content})
                if len(inner_self.snapshots) == 1:
                    (self.suite / "skills/translating-products/SKILL.md").write_text("mutated\n", encoding="utf-8")
                return FakeRunner.invoke(inner_self, prompt=prompt, project_dir=project_dir, timeout_seconds=timeout_seconds)

        runner = MutatingRunner()
        execute_schedule(
            suite_specs, runner, self.evidence,
            cases=cases, config=self.config(), templates=templates(),
        )

        self.assertEqual(runner.snapshots, [{"approved suite\n"}, {"approved suite\n"}])
        manifest = json.loads((self.evidence / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["input_snapshot"]["trees"]), {"skills", ".translation"})

        invalid_manifest = json.loads(json.dumps(manifest))
        invalid_manifest["input_snapshot"]["trees"]["skills"] = "not-a-sha256"
        atomic_write_json(self.evidence / "run-manifest.json", invalid_manifest)
        with self.assertRaisesRegex(BenchmarkError, "valid input snapshot binding"):
            execute_schedule(
                suite_specs, FakeRunner(), self.evidence,
                cases=cases, config=self.config(), templates=templates(),
            )

        manifest.pop("input_snapshot")
        atomic_write_json(self.evidence / "run-manifest.json", manifest)
        (self.suite / "skills/translating-products/SKILL.md").write_text(
            "second live mutation\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(BenchmarkError, "snapshot"):
            execute_schedule(
                suite_specs, FakeRunner(), self.evidence,
                cases=cases, config=self.config(), templates=templates(),
            )

    def test_prepared_manifest_hash_is_preserved_and_resume_checks_schedule(self):
        """Break: byte-hashed prepared configs could be rejected or changed schedules silently resumed."""
        case = one_translation_case()
        schedule = [RunSpec("resume-a", case["id"], "normal", 1)]
        self.evidence.mkdir()
        atomic_write_json(
            self.evidence / "run-manifest.json",
            {"schema_version": 1, "runner_config_sha256": "prepared-file-byte-hash"},
        )

        execute_schedule(
            schedule, FakeRunner("first"), self.evidence,
            cases=[case], config=self.config(), templates=templates(),
        )
        resumed_runner = FakeRunner("must not run")
        resumed = execute_schedule(
            schedule, resumed_runner, self.evidence,
            cases=[case], config=self.config(), templates=templates(),
        )

        manifest = json.loads((self.evidence / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["runner_config_sha256"], "prepared-file-byte-hash")
        self.assertIn("execution_config_sha256", manifest)
        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed_runner.prompts, [])
        with self.assertRaisesRegex(BenchmarkError, "schedule mismatch"):
            execute_schedule(
                [RunSpec("resume-b", case["id"], "normal", 1)],
                FakeRunner(), self.evidence,
                cases=[case], config=self.config(), templates=templates(),
            )

    def test_only_prestart_failure_retries_and_exception_still_cleans_project(self):
        """Break: retrying a started process or leaking a failed project biases later attempts."""
        case = one_translation_case()

        class PrestartThenSuccess(FakeRunner):
            def __init__(self):
                super().__init__("eventual output")
                self.calls = 0

            def invoke(self, *, prompt: str, project_dir: Path, timeout_seconds: int):
                self.calls += 1
                if self.calls == 1:
                    now = "2026-08-03T00:00:00Z"
                    return Invocation((), False, False, now, now, None, "", "", reason="ENOENT")
                return super().invoke(
                    prompt=prompt, project_dir=project_dir, timeout_seconds=timeout_seconds
                )

        retrying = PrestartThenSuccess()
        execute_schedule(
            [RunSpec("retry-a", case["id"], "normal", 1)],
            retrying, self.evidence / "retry",
            cases=[case], config=self.config(), templates=templates(),
        )
        self.assertEqual(retrying.calls, 2)

        class StartedFailure(FakeRunner):
            def __init__(self):
                super().__init__("", exit_code=1)
                self.calls = 0

            def invoke(self, **kwargs):
                self.calls += 1
                return super().invoke(**kwargs)

        started = StartedFailure()
        execute_schedule(
            [RunSpec("retry-b", case["id"], "normal", 1)],
            started, self.evidence / "started",
            cases=[case], config=self.config(), templates=templates(),
        )
        self.assertEqual(started.calls, 1)

        class ExplodingRunner:
            project_dir: Path | None = None

            def invoke(self, *, prompt: str, project_dir: Path, timeout_seconds: int):
                self.project_dir = project_dir
                raise RuntimeError("runner defect")

        exploding = ExplodingRunner()
        with self.assertRaisesRegex(RuntimeError, "runner defect"):
            execute_schedule(
                [RunSpec("cleanup-a", case["id"], "normal", 1)],
                exploding, self.evidence / "cleanup",
                cases=[case], config=self.config(), templates=templates(),
            )
        self.assertIsNotNone(exploding.project_dir)
        self.assertFalse(exploding.project_dir.exists())

    def test_manual_round_trip_rejects_missing_duplicate_unknown_and_malformed_ids(self):
        """Break: an incomplete or misidentified manual response set could be accepted as evidence."""
        case = one_translation_case()
        schedule = [
            RunSpec("manual-a", case["id"], "normal", 1),
            RunSpec("manual-b", case["id"], "suite", 1),
        ]
        packages = self.temp_dir / "packages"
        exported = export_manual_packages(
            schedule,
            packages,
            cases=[case],
            config=self.config(),
            templates=templates(),
        )
        self.assertEqual(len(exported), 2)
        self.assertEqual(
            set(json.loads(exported[0].read_text(encoding="utf-8"))),
            {"run_id", "prompt", "settings"},
        )

        responses = self.temp_dir / "responses"
        responses.mkdir()
        (responses / "a.json").write_text(
            json.dumps({"run_id": "manual-a", "response": "Resposta A"}), encoding="utf-8"
        )
        with self.assertRaisesRegex(BenchmarkError, "missing"):
            import_manual_responses(schedule, responses, self.evidence, config=self.config())
        (responses / "b.json").write_text(
            json.dumps({"run_id": "manual-b", "response": "Resposta B"}), encoding="utf-8"
        )
        (responses / "duplicate.json").write_text(
            json.dumps({"run_id": "manual-a", "response": "Outra"}), encoding="utf-8"
        )
        with self.assertRaisesRegex(BenchmarkError, "duplicate"):
            import_manual_responses(schedule, responses, self.evidence, config=self.config())
        (responses / "duplicate.json").write_text(
            json.dumps({"run_id": "unknown", "response": "Outra"}), encoding="utf-8"
        )
        with self.assertRaisesRegex(BenchmarkError, "unknown"):
            import_manual_responses(schedule, responses, self.evidence, config=self.config())
        (responses / "duplicate.json").unlink()
        (responses / "b.json").write_bytes(b"\xff")
        with self.assertRaisesRegex(BenchmarkError, "UTF-8"):
            import_manual_responses(schedule, responses, self.evidence, config=self.config())

    def test_manual_import_has_the_same_record_keys_as_fake_execution(self):
        """Break: manual evidence with a divergent schema could not share downstream scoring."""
        case = one_translation_case()
        fake_spec = RunSpec("fake", case["id"], "normal", 1)
        fake_result = execute_schedule(
            [fake_spec], FakeRunner("Resposta"), self.evidence / "fake",
            cases=[case], config=self.config(), templates=templates(),
        )[0]
        manual_spec = RunSpec("manual", case["id"], "normal", 1)
        responses = self.temp_dir / "manual-responses"
        responses.mkdir()
        (responses / "response.json").write_text(
            json.dumps({"run_id": "manual", "response": "Resposta"}), encoding="utf-8"
        )

        manual_result = import_manual_responses(
            [manual_spec], responses, self.evidence / "manual", config=self.config()
        )[0]

        self.assertEqual(set(fake_result.to_record()), set(manual_result.to_record()))


if __name__ == "__main__":
    unittest.main()
