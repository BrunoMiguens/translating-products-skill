"""Check the saved Android resource directory for a Hebrew locale."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locale", required=True)
    parser.add_argument("--runtime-verified-directory")
    parser.add_argument("resource", type=Path)
    args = parser.parse_args()

    if not args.resource.is_file():
        parser.error(f"saved resource does not exist: {args.resource}")

    tags = args.locale.lower().replace("_", "-").split("-")
    if tags[0] not in {"he", "iw"}:
        return 0

    subtag_after_language = tags[1:]
    if subtag_after_language and len(subtag_after_language[0]) == 4 and subtag_after_language[0].isalpha():
        subtag_after_language = subtag_after_language[1:]

    expected = ["values-iw"]
    if subtag_after_language[:1] == ["il"]:
        expected.append("values-iw-rIL")
    if args.runtime_verified_directory is not None:
        verified = args.runtime_verified_directory
        if not verified.startswith("values-") or Path(verified).name != verified or verified == "values-":
            parser.error("verified directory must be an alternate Android values directory name")
        expected = [verified]

    directory = args.resource.parent.name
    if directory not in expected:
        parser.error(f"{directory} cannot be used for {args.locale}. Expected {' or '.join(expected)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
