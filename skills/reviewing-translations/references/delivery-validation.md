# Verify reviewed values in final resources

Use the canonical request and result for the current release. After integration, run the validator against the files the caller will receive.

```sh
python3 SKILL_DIRECTORY/scripts/validate_review_artifact.py \
  --request review-request.json --result review-result.json \
  --delivery delivery.json
```

Build the delivery file from the project's actual resource paths and keys. Use the same review ID and include all reviewed locales and units. Relative paths start at the delivery file’s directory. This example maps one reviewed unit to a JSON resource.

```json
{
  "review_id": "account-settings-fr",
  "locales": {
    "fr-FR": {
      "actions.save": {"path": "locales/fr-FR.json", "keys": ["actions", "save"]}
    }
  }
}
```

- Use JSON object keys and zero-based array indices in `keys`. Use `[]` for the root or `null` for a whole UTF-8 text file.
- Use `"typed_json": true` when the reviewed text itself encodes a JSON value such as an object of named plural forms. Object order and JSON escape spelling can vary. Array order, field names, decoded values and types must match.
- Use `"format": "gettext"` and `"keys": ["msgid"]` to check a compiled GNU `.mo` file. Provide `context` for a contextual entry and `count` to check a selected plural value. Without a count, the lookup uses the stored singular entry or the stored branch selected at one. Check all applicable reviewed arms through their respective counts. An absent entry fails even when the runtime would fall back to the same source text.
- A unit assessed without an issue must match the reviewed text. A recommended change must be applied exactly as in the passing correction review. Unresolved or source-blocked units fail this full-delivery check.
- A value mismatch names the locale and unit for renewed review. A successful check confirms value correspondence at the supplied locations. The caller still owns authoritative path selection, native compiler checks, visual verification and the output format.

For a caller-approved partial delivery, add `--allow-partial-delivery` and a top-level `withheld` map of locales to unresolved or source-blocked unit ID lists. Keep a `locales` entry for every reviewed locale, even if it releases no units. The validator checks that released and withheld IDs account for the complete review. Package only the intended subset and check that the public artifact excludes any withheld wording before claiming a partial release.

The optional argument leaves the canonical v2 artifacts and existing validator response unchanged. Other native formats need their own consumer lookup.
