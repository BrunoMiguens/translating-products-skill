# Check an approved gettext plural rule

Use the plural contract for the exact product version and locale variant. Record the publisher evidence with the project decision. Choose representative counts that distinguish the supported forms, including boundaries and any cases that select a different index from the default locale.

For example, a project that uses index 0 for zero and one and index 1 for other counts can record:

```json
{
  "nplurals": 2,
  "selections": {"0": 0, "1": 0, "2": 1, "101": 1}
}
```

Compile the actual localized file and check its declared arity and selector.

```sh
msgfmt --check -o candidate.mo candidate.po &&
  python3 SKILL_DIRECTORY/scripts/validate_gettext_plural_contract.py candidate.mo approved-plural-contract.json
```

To check every plural form of selected messages, add a `required_messages` array to the approved contract. Each entry has a `msgid` and, where applicable, its exact `context`. The checker requires a stored, nonempty value for every branch of each listed message.

The script loads the compiled file with Python's `gettext.GNUTranslations`. It verifies the sampled counts and reports every mismatched index. A match records sampled agreement in that consumer. If the application uses GNU libintl or another gettext implementation, use that runtime to verify its actual branch selection and localized entries.

If the publisher contract is unavailable, record the missing input. A generic language table provides background for the decision. Use the approved product rule for release.
