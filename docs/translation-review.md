# Help review translations

If you know a language well enough to compare it with the source, you can help
improve the skills. Reviewing a single string or paragraph is enough to start.
Hebrew, Japanese, and Swahili are current priorities. Reviews in other
languages are welcome.

[Submit a translation review](https://github.com/BrunoMiguens/translating-products-skill/issues/new?template=translation-review.md)
with a public source or a short example you can share. You can review wording in
an existing issue or contribute an example from a product you know. You can
also [run the skill locally](getting-started.md) and review the result. Include
the skill revision when you have it. Release reviews need the full candidate
commit SHA and the exact fixture and output reviewed.

## Review one case

1. Read the source and translation in context. Record the target locale,
   audience, and where the text appears. Note any relevant product terminology,
   character limit, placeholder, or markup.
2. Check that a reader would understand the same meaning and take the same
   action. Look for changed roles, timing, facts, or technical distinctions.
   Check whether the wording sounds natural for the locale and surface.
3. Explain the effect of any defect and suggest wording if you have a sound
   alternative. Mark preferences and missing context explicitly. It is also
   useful to confirm that the current wording works.
4. Submit the source, translation reviewed, and evidence in the template.
   Use a public source link or your own small example. GitHub issues and their
   attachments are public. Keep private text and identifying details out of
   the submission.

### Practice examples

These are synthetic drafts you can review as they are. When filing an issue
about one, link to this section and name the example. For a skill defect, also
include the result from the skill version you tested.

| Locale | English source | Draft to review | What to check |
| --- | --- | --- | --- |
| Hebrew | Select the backup you created on this device. | יש לבחור את הגיבוי שנוצר במכשיר הזה. | Check whether the instruction identifies who created the backup and sounds natural for the configured form of address. |
| Japanese | Verify the archive checksum before extracting the files. | 展開するファイルに先立ち、アーカイブのチェックサムを確認します。 | Check when the checksum must be verified. |
| Swahili | The transport uses a cyclic redundancy check (CRC). A failed check indicates damaged data. | Usafirishaji hutumia ukaguzi (CRC). Ukaguzi ukishindwa, data imeharibika. | Check the technical expansion and what the failure tells the reader. |
| Swahili | The validator locks tokens as collateral. The investor holds shares in the company. | Mthibitishaji anafunga tokeni kama dhamana. Mwekezaji anamiliki hisa katika kampuni. | Check whether the two financial concepts remain distinct. |

## How submissions are used

Maintainers check the source, the scope of the review, and any competing
interpretations. A finding may become a regression example or a change to a
skill. If you want to propose a patch, follow the
[contribution guide](../CONTRIBUTING.md).

Maintainers confirm the commit, fixture, output, and scope, then record the
decision in the issue. For the bilingual release gate, they link the issue in
the release evidence and record the contributor handle, locale, date, and
findings for the exact candidate SHA. A review covers the examples and aspects
it actually checked. Your GitHub handle is enough for credit. The stable tag
stays pending until the [release checklist](release-checklist.md) is complete.
