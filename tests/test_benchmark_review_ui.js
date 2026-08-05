#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const logic = require("../scripts/benchmark/review_ui/app.js");

assert.deepEqual(logic.labelsFor({ outputs: { A: "a", B: "b" } }), ["A", "B"]);
assert.deepEqual(logic.pairsFor(["A", "B"]), [["A", "B"]]);
assert.deepEqual(logic.pairsFor(["A", "B", "C"]), [
  ["A", "B"], ["A", "C"], ["B", "C"],
]);

assert.equal(logic.nextRevision("item-1", { latest: {} }), 1);
assert.equal(logic.nextRevision("item-1", { latest: { "item-1": { revision: 2 } } }), 3);
assert.equal(logic.pythonLength("Paga já 😊"), 9);
assert.equal(logic.validPythonSpan("Paga já 😊", 8, 9), true);
assert.equal(logic.validPythonSpan("Paga já 😊", 8, 10), false);

assert.equal(logic.shortcutDirection({ key: "[", defaultPrevented: false, target: { tagName: "DIV" } }), -1);
assert.equal(logic.shortcutDirection({ key: "]", defaultPrevented: false, target: { tagName: "DIV" } }), 1);
assert.equal(logic.shortcutDirection({ key: "[", defaultPrevented: false, target: { tagName: "INPUT" } }), 0);
assert.equal(logic.shortcutDirection({ key: "]", defaultPrevented: false, target: { tagName: "TEXTAREA" } }), 0);
assert.equal(logic.shortcutDirection({ key: "[", defaultPrevented: true, target: { tagName: "DIV" } }), 0);

assert.equal(logic.allowNavigation(true, () => false), false);
assert.equal(logic.allowNavigation(true, () => true), true);
assert.equal(logic.allowNavigation(false, () => false), true);
assert.equal(logic.shouldWarnBeforeUnload({ dirty: true }), true);
assert.equal(logic.shouldWarnBeforeUnload({ dirty: false }), false);
assert.equal(logic.shouldWarnBeforeUnload({ dirty: false, saving: true }), true);

const complete = { total: 198, remaining: 0, locked: false };
assert.equal(logic.lockAllowed(complete, { dirty: false, saving: false }), true);
assert.equal(logic.lockAllowed(complete, { dirty: true, saving: false }), false);
assert.equal(logic.lockAllowed(complete, { dirty: false, saving: true }), false);
assert.equal(logic.lockAllowed({ ...complete, remaining: 1 }, { dirty: false, saving: false }), false);
assert.equal(logic.lockAllowed({ ...complete, locked: true }, { dirty: false, saving: false }), false);

assert.deepEqual(
  logic.interactionState({ locked: true }, { saving: false }, 1, 3),
  { formLocked: true, previousDisabled: false, nextDisabled: false },
);
assert.deepEqual(
  logic.interactionState({ locked: true }, { saving: false }, 0, 3),
  { formLocked: true, previousDisabled: true, nextDisabled: false },
);
assert.deepEqual(
  logic.interactionState({ locked: true }, { saving: false }, 2, 3),
  { formLocked: true, previousDisabled: false, nextDisabled: true },
);
assert.deepEqual(
  logic.interactionState({ locked: false }, { saving: true }, 1, 3),
  { formLocked: true, previousDisabled: true, nextDisabled: true },
);
assert.deepEqual(
  logic.interactionState({ locked: false }, { saving: false }, 1, 3),
  { formLocked: false, previousDisabled: false, nextDisabled: false },
);
assert.deepEqual(
  logic.interactionState({ locked: false }, { saving: false }, 0, 3),
  { formLocked: false, previousDisabled: true, nextDisabled: false },
);
assert.deepEqual(
  logic.interactionState({ locked: false }, { saving: false }, 2, 3),
  { formLocked: false, previousDisabled: false, nextDisabled: true },
);

assert.deepEqual(logic.saveSettlement(4, 4), { dirty: false, newerEdits: false });
assert.deepEqual(logic.saveSettlement(4, 5), { dirty: true, newerEdits: true });

const queueProgress = { max: 0, value: 0, textContent: "" };
const proofProgress = { max: 0, value: 0 };
logic.applyProgress(queueProgress, proofProgress, { total: 198, completed: 47 });
assert.deepEqual(
  { max: queueProgress.max, value: queueProgress.value, text: queueProgress.textContent },
  { max: 198, value: 47, text: "47 of 198 saved" },
);
assert.deepEqual({ max: proofProgress.max, value: proofProgress.value }, { max: 198, value: 47 });
assert.equal(Object.hasOwn(proofProgress, "style"), false);

process.stdout.write("review UI behavior: PASS\n");
