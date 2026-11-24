# Measure the final field against its budget

Take the limit and counting method from the approved product contract for the field and platform version. Check the complete field that the exporter or upload will use, including fixed prefixes, suffixes, spaces and line breaks.

Apply a `character_limit` check to that assembled value. For example, when the approved budget is 30 UTF-16 code units:

```json
{"type":"character_limit","severity":"critical","max":30,"counting":"utf16"}
```

`codepoints` counts Unicode code points and remains the default. `utf16` counts UTF-16 code units and `utf8` counts UTF-8 bytes. Use the platform tool for other counting methods, such as a limit defined by grapheme clusters. If the field cannot meet its budget while preserving a required product claim, identify the conflict for the field owner.
