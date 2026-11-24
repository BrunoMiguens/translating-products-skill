import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest


CHECKER = (
    Path(__file__).resolve().parents[1]
    / "skills/localizing-software/scripts/validate_gettext_plural_contract.py"
)


def write_catalog(path, rule):
    metadata = f"Content-Type: text/plain; charset=UTF-8\nPlural-Forms: {rule}\n".encode()
    header_size = 7 * 4
    strings_start = header_size + 2 * 8
    path.write_bytes(
        struct.pack("<7I", 0x950412DE, 0, 1, header_size, header_size + 8, 0, 0)
        + struct.pack("<4I", 0, strings_start, len(metadata), strings_start + 1)
        + b"\0" + metadata + b"\0"
    )


class GettextPluralContractTests(unittest.TestCase):
    def check(self, rule, authority=None, *, compile_native=False):
        with tempfile.TemporaryDirectory() as location:
            root = Path(location)
            po = root / "catalog.po"
            mo = root / "catalog.mo"
            packet = root / "plural-contract.json"
            if compile_native:
                po.write_text(
                    'msgid ""\nmsgstr ""\n'
                    '"Content-Type: text/plain; charset=UTF-8\\n"\n'
                    f'"Plural-Forms: {rule}\\n"\n\n'
                    'msgid "marker"\nmsgid_plural "markers"\n'
                    + "".join(f'msgstr[{i}] "{value}"\n' for i, value in enumerate(("zero-one", "many"))),
                    encoding="utf-8",
                )
                subprocess.run(["msgfmt", "-o", str(mo), str(po)], capture_output=True, check=True)
            else:
                write_catalog(mo, rule)
            packet.write_text(json.dumps(authority or {
                "nplurals": 2, "selections": {"0": 0, "1": 0, "2": 1, "101": 1},
            }), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(CHECKER), str(mo), str(packet)],
                capture_output=True, text=True,
            )

    def test_compiled_catalog_with_wrong_arity_is_rejected(self):
        result = self.check("nplurals=3; plural=(n>1);")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("arity", result.stderr)

    def test_same_arity_wrong_count_selection_is_rejected(self):
        result = self.check("nplurals=2; plural=(n!=1);")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("count 0", result.stderr)

    def test_equivalent_expressions_pass_the_same_samples(self):
        for expression in ("n>1", "n==0||n==1?0:1"):
            with self.subTest(expression=expression):
                result = self.check(f"nplurals=2; plural={expression};")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {"checked_counts": 4, "nplurals": 2})

    def test_invalid_authority_branch_and_count_are_rejected(self):
        for sample in ({"0": 2}, {"01": 0}, {"1": False}, {}):
            with self.subTest(sample=sample):
                result = self.check("nplurals=2; plural=n>1;",
                                    {"nplurals": 2, "selections": sample})
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("authority", result.stderr)


    def test_malformed_authority_uses_the_input_error_exit(self):
        with tempfile.TemporaryDirectory() as location:
            root = Path(location)
            authority = root / "authority.json"
            for text in ('{"nplurals":1,"nplurals":2,"selections":{"0":0}}', "[" * 1500 + "0" + "]" * 1500):
                with self.subTest(duplicated="{" in text):
                    authority.write_text(text, encoding="utf-8")
                    result = subprocess.run(
                        [sys.executable, str(CHECKER), str(root / "missing.mo"), str(authority)],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("invalid authority", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    @unittest.skipUnless(shutil.which("msgfmt"), "GNU msgfmt is required")
    def test_real_compiler_catalog_uses_the_same_selector_check(self):
        result = self.check("nplurals=2; plural=(n!=1);", compile_native=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("count 0", result.stderr)
