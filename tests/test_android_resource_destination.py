from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


CHECK = (
    Path(__file__).resolve().parents[1]
    / "skills/translating-android/scripts/check_resource_destination.py"
)


class AndroidResourceDestinationTests(unittest.TestCase):
    def run_check(self, directory, locale, verified=None):
        with TemporaryDirectory() as root:
            resource = Path(root, "res", directory, "strings.xml")
            resource.parent.mkdir(parents=True)
            resource.write_text("<resources/>", encoding="utf-8")
            args = [sys.executable, str(CHECK), "--locale", locale, str(resource)]
            if verified is not None:
                args.extend(["--runtime-verified-directory", verified])
            return subprocess.run(args, capture_output=True, text=True)

    def test_hebrew_locale_resolves_the_legacy_directory(self):
        for directory, locale in (
            ("values-iw", "he"),
            ("values-iw", "he-IL"),
            ("values-iw", "iw"),
            ("values-iw-rIL", "he-IL"),
            ("values-iw-rIL", "he-Hebr-IL"),
        ):
            with self.subTest(directory=directory, locale=locale):
                self.assertEqual(self.run_check(directory, locale).returncode, 0)

    def test_compile_only_hebrew_directories_are_rejected(self):
        for directory, locale in (
            ("values-he", "he"),
            ("values-he-rIL", "he-IL"),
            ("values-b+he", "he"),
            ("values-b+he+IL", "he-IL"),
            ("values-iw-rIL", "he-US-x-il"),
            ("values-iw-rIL", "he-x-il"),
        ):
            with self.subTest(directory=directory, locale=locale):
                result = self.run_check(directory, locale)
                self.assertEqual(result.returncode, 2)
                self.assertIn("values-iw", result.stderr)

    def test_only_the_runtime_verified_destination_is_accepted(self):
        match = self.run_check("values-b+he+IL", "he-IL", "values-b+he+IL")
        mismatch = self.run_check("values-b+he", "he-IL", "values-b+he+IL")
        default = self.run_check("values", "he-IL", "values")
        self.assertEqual(match.returncode, 0)
        self.assertEqual(mismatch.returncode, 2)
        self.assertEqual(default.returncode, 2)

    def test_other_languages_keep_their_current_directory(self):
        self.assertEqual(self.run_check("values-pl", "pl").returncode, 0)


if __name__ == "__main__":
    unittest.main()
