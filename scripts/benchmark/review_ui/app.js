"use strict";

const comparisonChoices = [
  ["left_clear", "Left clearly better"],
  ["left_slight", "Left slightly better"],
  ["tie", "Tie"],
  ["right_slight", "Right slightly better"],
  ["right_clear", "Right clearly better"],
];
const dimensions = [
  "accuracy", "terminology", "linguistic_quality", "style_register",
  "locale_audience", "product_integrity",
];
const severities = ["critical", "major", "minor", "neutral"];
const state = {
  bundle: null,
  review: null,
  index: 0,
  dirty: false,
  saving: false,
  editGeneration: 0,
};

const byId = (id) => document.getElementById(id);

function pythonLength(text) {
  return Array.from(text).length;
}

function validPythonSpan(text, start, end) {
  return Number.isInteger(start)
    && Number.isInteger(end)
    && start >= 0
    && end > start
    && end <= pythonLength(text);
}

function nextRevision(itemId, reviewState) {
  return (reviewState.latest[itemId]?.revision || 0) + 1;
}

function isTextEntry(target) {
  if (!target || typeof target !== "object") return false;
  const tagName = typeof target.tagName === "string" ? target.tagName.toUpperCase() : "";
  return target.isContentEditable === true || ["INPUT", "TEXTAREA", "SELECT"].includes(tagName);
}

function shortcutDirection(event) {
  if (event.defaultPrevented || isTextEntry(event.target)) return 0;
  if (event.key === "[") return -1;
  if (event.key === "]") return 1;
  return 0;
}

function allowNavigation(dirty, confirmDiscard) {
  return !dirty || confirmDiscard();
}

function shouldWarnBeforeUnload(uiState) {
  return uiState.dirty === true || uiState.saving === true;
}

function lockAllowed(reviewState, uiState) {
  return reviewState.total === 198
    && reviewState.remaining === 0
    && reviewState.locked === false
    && uiState.dirty === false
    && uiState.saving === false;
}

function interactionState(reviewState, uiState, index, itemCount) {
  const navigationDisabled = uiState.saving === true;
  return {
    formLocked: reviewState.locked === true || navigationDisabled,
    previousDisabled: navigationDisabled || index <= 0,
    nextDisabled: navigationDisabled || index >= itemCount - 1,
  };
}

function saveSettlement(submittedGeneration, currentGeneration, formChanged = false) {
  const newerEdits = submittedGeneration !== currentGeneration || formChanged;
  return { dirty: newerEdits, newerEdits };
}

function applyProgress(queueProgress, proofProgress, reviewState) {
  queueProgress.max = reviewState.total;
  queueProgress.value = reviewState.completed;
  queueProgress.textContent = `${reviewState.completed} of ${reviewState.total} saved`;
  proofProgress.max = reviewState.total;
  proofProgress.value = reviewState.completed;
}

function displayValue(value) {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function setError(message = "") {
  byId("error-status").textContent = message;
}

function setSaveStatus(message, statusName) {
  const status = byId("save-status");
  status.textContent = message;
  status.dataset.state = statusName;
}

function markDirty() {
  if (state.review?.locked) return;
  state.editGeneration += 1;
  state.dirty = true;
  setSaveStatus(
    state.saving ? "Saving snapshot · newer edits pending" : "Unsaved changes",
    "dirty",
  );
  if (state.review) updateProgress();
}

function labelsFor(item) {
  return ["A", "B", "C"].filter((label) => Object.hasOwn(item.outputs, label));
}

function pairsFor(labels) {
  const pairs = [];
  labels.forEach((left, index) => {
    labels.slice(index + 1).forEach((right) => pairs.push([left, right]));
  });
  return pairs;
}

function hasComparisonCycle(labels, comparisons) {
  const parent = Object.fromEntries(labels.map((label) => [label, label]));
  const find = (label) => {
    while (parent[label] !== label) {
      parent[label] = parent[parent[label]];
      label = parent[label];
    }
    return label;
  };
  for (const [key, value] of Object.entries(comparisons)) {
    if (value !== "tie") continue;
    const [left, right] = key.split(":");
    const leftRoot = find(left);
    const rightRoot = find(right);
    if (leftRoot !== rightRoot) parent[rightRoot] = leftRoot;
  }
  const edges = {};
  for (const [key, value] of Object.entries(comparisons)) {
    if (value === "tie") continue;
    const [left, right] = key.split(":");
    const leftWins = value.startsWith("left_");
    const better = find(leftWins ? left : right);
    const worse = find(leftWins ? right : left);
    if (better === worse) return true;
    (edges[better] ||= new Set()).add(worse);
  }
  const visiting = new Set();
  const visited = new Set();
  const visit = (label) => {
    if (visiting.has(label)) return true;
    if (visited.has(label)) return false;
    visiting.add(label);
    for (const child of edges[label] || []) {
      if (visit(child)) return true;
    }
    visiting.delete(label);
    visited.add(label);
    return false;
  };
  return labels.some((label) => visit(find(label)));
}

function makeOption(value, text = value) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = text.replaceAll("_", " ");
  return option;
}

function renderMetadata(container, value) {
  container.replaceChildren();
  if (value && typeof value === "object" && !Array.isArray(value)) {
    Object.entries(value).forEach(([key, item]) => {
      const term = document.createElement("dt");
      term.textContent = key.replaceAll("_", " ");
      const definition = document.createElement("dd");
      definition.textContent = displayValue(item);
      container.append(term, definition);
    });
    return;
  }
  const text = document.createElement("span");
  text.textContent = displayValue(value);
  container.append(text);
}

function renderOutputs(item) {
  const list = byId("output-list");
  const labels = labelsFor(item);
  list.dataset.count = String(labels.length);
  list.replaceChildren();
  labels.forEach((label) => {
    const article = document.createElement("article");
    article.className = "output-card";
    article.setAttribute("aria-labelledby", `output-${label}-heading`);
    const heading = document.createElement("h3");
    heading.id = `output-${label}-heading`;
    heading.textContent = `Output ${label}`;
    const text = document.createElement("pre");
    text.id = `output-${label}-text`;
    text.textContent = item.outputs[label];
    article.append(heading, text);
    list.append(article);
  });
}

function renderComparisons(item, latest) {
  const container = byId("comparison-controls");
  container.replaceChildren();
  pairsFor(labelsFor(item)).forEach(([left, right]) => {
    const key = `${left}:${right}`;
    const fieldset = document.createElement("fieldset");
    fieldset.className = "comparison-group";
    const legend = document.createElement("legend");
    legend.textContent = `Output ${left} compared with output ${right}`;
    const options = document.createElement("div");
    options.className = "comparison-options";
    comparisonChoices.forEach(([value, labelText]) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = `comparison-${key}`;
      input.value = value;
      input.required = true;
      input.dataset.comparison = key;
      input.checked = latest?.comparisons?.[key] === value;
      label.append(input, document.createTextNode(labelText));
      options.append(label);
    });
    fieldset.append(legend, options);
    container.append(fieldset);
  });
}

function renderMajorControls(item, latest) {
  const container = byId("major-controls");
  container.replaceChildren();
  labelsFor(item).forEach((output) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.majorOutput = output;
    input.checked = latest?.major_or_worse?.[output] === true;
    input.disabled = true;
    label.append(input, document.createTextNode(`Output ${output}`));
    container.append(label);
  });
}

function labelledControl(labelText, control, className = "") {
  const label = document.createElement("label");
  if (className) label.className = className;
  label.append(document.createTextNode(labelText), control);
  return label;
}

function addMqmRow(value = null) {
  const item = state.bundle.items[state.index];
  const row = document.createElement("div");
  row.className = "mqm-row";

  const output = document.createElement("select");
  output.className = "mqm-output";
  output.required = true;
  labelsFor(item).forEach((label) => output.append(makeOption(label, `Output ${label}`)));
  output.value = value?.output || labelsFor(item)[0];

  const dimension = document.createElement("select");
  dimension.className = "mqm-dimension";
  dimension.required = true;
  dimensions.forEach((name) => dimension.append(makeOption(name)));
  dimension.value = value?.dimension || "accuracy";

  const severity = document.createElement("select");
  severity.className = "mqm-severity";
  severity.required = true;
  severities.forEach((name) => severity.append(makeOption(name)));
  severity.value = value?.severity || "minor";

  const start = document.createElement("input");
  start.className = "mqm-start";
  start.type = "number";
  start.min = "0";
  start.step = "1";
  start.required = true;
  start.value = value?.start ?? 0;

  const end = document.createElement("input");
  end.className = "mqm-end";
  end.type = "number";
  end.min = "1";
  end.step = "1";
  end.required = true;
  end.value = value?.end ?? 1;

  const note = document.createElement("input");
  note.className = "mqm-note-input";
  note.type = "text";
  note.required = true;
  note.value = value?.note || "";

  function updateOffsetMaximum() {
    const maximum = pythonLength(item.outputs[output.value]);
    start.max = String(Math.max(0, maximum - 1));
    end.max = String(maximum);
  }
  output.addEventListener("change", () => {
    updateOffsetMaximum();
    updateMajorFlags();
  });
  severity.addEventListener("change", updateMajorFlags);
  updateOffsetMaximum();

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove-mqm";
  remove.textContent = "Remove";
  remove.setAttribute("aria-label", "Remove this MQM row");
  remove.addEventListener("click", () => {
    row.remove();
    updateMqmEmpty();
    updateMajorFlags();
    markDirty();
  });

  row.append(
    labelledControl("Output", output),
    labelledControl("Dimension", dimension),
    labelledControl("Severity", severity),
    labelledControl("Start", start),
    labelledControl("End", end),
    labelledControl("Finding note", note, "mqm-note"),
    remove,
  );
  byId("mqm-rows").append(row);
  updateMqmEmpty();
  updateMajorFlags();
}

function updateMqmEmpty() {
  byId("mqm-empty").hidden = byId("mqm-rows").children.length > 0;
}

function updateMajorFlags() {
  const majorOutputs = new Set();
  document.querySelectorAll(".mqm-row").forEach((row) => {
    if (["critical", "major"].includes(row.querySelector(".mqm-severity").value)) {
      majorOutputs.add(row.querySelector(".mqm-output").value);
    }
  });
  document.querySelectorAll("[data-major-output]").forEach((control) => {
    control.checked = majorOutputs.has(control.dataset.majorOutput);
  });
}

function renderMqm(latest) {
  byId("mqm-rows").replaceChildren();
  (latest?.mqm || []).forEach((finding) => addMqmRow(finding));
  updateMqmEmpty();
  updateMajorFlags();
}

function setFormLocked(locked) {
  byId("annotation-form").querySelectorAll("input, select, textarea, button").forEach((control) => {
    if (!control.matches("[data-major-output]")) control.disabled = locked;
  });
}

function setInteractionState() {
  const controls = interactionState(
    state.review, state, state.index, state.bundle.items.length,
  );
  setFormLocked(controls.formLocked);
  byId("previous-item").disabled = controls.previousDisabled;
  byId("next-item").disabled = controls.nextDisabled;
}

function renderCurrent() {
  const item = state.bundle.items[state.index];
  const latest = state.review.latest[item.id] || null;
  byId("item-position").textContent = `${state.index + 1} of ${state.bundle.items.length}`;
  byId("item-id").textContent = item.id;
  byId("source-text").textContent = item.source;
  renderMetadata(byId("context-content"), item.context);
  byId("constraints-content").textContent = displayValue(item.constraints);
  const candidateRegion = byId("candidate-region");
  candidateRegion.hidden = !Object.hasOwn(item, "candidate");
  byId("candidate-text").textContent = item.candidate || "";
  renderOutputs(item);
  renderComparisons(item, latest);
  renderMajorControls(item, latest);
  renderMqm(latest);
  document.querySelectorAll('input[name="confidence"]').forEach((input) => {
    input.checked = latest?.confidence === input.value;
  });
  byId("review-note").value = latest?.note || "";
  byId("validation-summary").textContent = "";
  setError();
  state.dirty = false;
  state.editGeneration += 1;
  setSaveStatus(
    latest ? `Saved · revision ${latest.revision}` : "Not yet saved",
    latest ? "saved" : "empty",
  );
  setInteractionState();
  byId("review-workspace").focus({ preventScroll: true });
}

function updateProgress() {
  const { completed, total, remaining, locked } = state.review;
  byId("progress-count").textContent = `${completed} / ${total}`;
  const progress = byId("queue-progress");
  const proofProgress = byId("proof-progress");
  applyProgress(progress, proofProgress, state.review);
  byId("queue-summary").textContent = locked
    ? "Annotations locked. This session is read-only."
    : `${remaining} presentation${remaining === 1 ? "" : "s"} remaining.`;
  const lockEnabled = lockAllowed(state.review, state);
  byId("lock-annotations").disabled = !lockEnabled;
  byId("reviewer-id").disabled = locked || state.saving;
  byId("lock-status").textContent = locked
    ? `Locked by ${state.review.lock_record?.reviewer_id || "reviewer"}.`
    : lockEnabled
      ? "All 198 presentations are saved. Locking is irreversible."
      : "Available after all 198 presentations are saved.";
}

function collectMqm(item) {
  return Array.from(document.querySelectorAll(".mqm-row")).map((row, index) => {
    const finding = {
      output: row.querySelector(".mqm-output").value,
      dimension: row.querySelector(".mqm-dimension").value,
      severity: row.querySelector(".mqm-severity").value,
      start: Number(row.querySelector(".mqm-start").value),
      end: Number(row.querySelector(".mqm-end").value),
      note: row.querySelector(".mqm-note-input").value,
    };
    const length = pythonLength(item.outputs[finding.output]);
    if (!validPythonSpan(item.outputs[finding.output], finding.start, finding.end)) {
      throw new Error(`MQM row ${index + 1} needs a span inside output ${finding.output} (0–${length}).`);
    }
    if (!finding.note.trim()) throw new Error(`MQM row ${index + 1} needs a finding note.`);
    return finding;
  });
}

function collectAnnotation() {
  const item = state.bundle.items[state.index];
  const comparisons = {};
  for (const [left, right] of pairsFor(labelsFor(item))) {
    const key = `${left}:${right}`;
    const checked = document.querySelector(`input[name="comparison-${key}"]:checked`);
    if (!checked) throw new Error(`Choose a comparison for outputs ${left} and ${right}.`);
    comparisons[key] = checked.value;
  }
  if (hasComparisonCycle(labelsFor(item), comparisons)) {
    throw new Error("Resolve the contradictory three-way comparison cycle before saving.");
  }
  const confidence = document.querySelector('input[name="confidence"]:checked');
  if (!confidence) throw new Error("Choose a confidence level.");
  const mqm = collectMqm(item);
  const major_or_worse = Object.fromEntries(labelsFor(item).map((label) => [
    label,
    mqm.some((finding) => finding.output === label && ["critical", "major"].includes(finding.severity)),
  ]));
  return {
    item_id: item.id,
    revision: nextRevision(item.id, state.review),
    comparisons,
    confidence: confidence.value,
    mqm,
    major_or_worse,
    note: byId("review-note").value,
  };
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error?.message || `Request failed (${response.status}).`);
  return payload;
}

async function saveCurrent(event) {
  event.preventDefault();
  if (state.review.locked || state.saving) return;
  byId("validation-summary").textContent = "";
  setError();
  let annotation;
  try {
    annotation = collectAnnotation();
  } catch (error) {
    byId("validation-summary").textContent = error.message;
    return;
  }
  const submittedGeneration = state.editGeneration;
  state.saving = true;
  setInteractionState();
  updateProgress();
  setSaveStatus("Saving revision…", "saving");
  try {
    const payload = await requestJson("/api/annotations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(annotation),
    });
    let formChanged = true;
    try {
      formChanged = JSON.stringify(collectAnnotation()) !== JSON.stringify(annotation);
    } catch (_error) {
      formChanged = true;
    }
    const settlement = saveSettlement(
      submittedGeneration, state.editGeneration, formChanged,
    );
    state.review = payload.state;
    state.review.latest[annotation.item_id] = payload.annotation;
    state.dirty = settlement.dirty;
    setSaveStatus(
      settlement.newerEdits
        ? `Saved revision ${payload.annotation.revision} · newer edits unsaved`
        : `Saved · revision ${payload.annotation.revision}`,
      settlement.newerEdits ? "dirty" : "saved",
    );
    updateProgress();
  } catch (error) {
    state.dirty = true;
    setError(error.message);
    setSaveStatus("Save failed · changes retained", "dirty");
  } finally {
    state.saving = false;
    setInteractionState();
    updateProgress();
  }
}

function canLeaveCurrent() {
  return allowNavigation(
    state.dirty,
    () => window.confirm("This presentation has unsaved changes. Leave without saving them?"),
  );
}

function moveBy(amount) {
  const next = state.index + amount;
  if (state.saving || next < 0 || next >= state.bundle.items.length || !canLeaveCurrent()) return;
  state.index = next;
  renderCurrent();
  window.scrollTo({ top: 0, behavior: "auto" });
}

async function lockAnnotations(event) {
  event.preventDefault();
  if (!lockAllowed(state.review, state)) {
    setError(
      state.dirty
        ? "Save or discard the visible unsaved revision before locking."
        : "Locking is available only after all 198 presentations are saved.",
    );
    return;
  }
  const reviewerId = byId("reviewer-id").value;
  if (!reviewerId.trim()) {
    setError("Enter the reviewer identity before locking.");
    byId("reviewer-id").focus();
    return;
  }
  const attestation = {
    schema_version: 1,
    reviewer_locale: "pt-PT",
    pt_pt_proficient: byId("attest-proficient").checked,
    independence_and_conflicts: byId("reviewer-conflicts").value,
    continued_blindness_acknowledged: byId("attest-blind").checked,
    condition_key_not_accessed: byId("attest-key").checked,
    automated_findings_not_accessed: byId("attest-findings").checked,
    rubric_completed: byId("attest-rubric").checked,
  };
  if (!attestation.independence_and_conflicts.trim()
      || !attestation.pt_pt_proficient
      || !attestation.continued_blindness_acknowledged
      || !attestation.condition_key_not_accessed
      || !attestation.automated_findings_not_accessed
      || !attestation.rubric_completed) {
    setError("Complete every reviewer attestation before locking.");
    return;
  }
  if (!window.confirm("Lock all annotations now? No further revisions can be saved.")) return;
  state.saving = true;
  setInteractionState();
  updateProgress();
  try {
    const payload = await requestJson("/api/lock", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer_id: reviewerId, attestation }),
    });
    state.review = payload.state;
    state.review.lock_record = payload.lock;
    state.dirty = false;
    setSaveStatus("Annotations locked · read-only", "saved");
    updateProgress();
    setFormLocked(true);
  } catch (error) {
    setError(error.message);
  } finally {
    state.saving = false;
    setInteractionState();
    updateProgress();
  }
}

function installEvents() {
  byId("annotation-form").addEventListener("submit", saveCurrent);
  byId("annotation-form").addEventListener("input", markDirty);
  byId("annotation-form").addEventListener("change", markDirty);
  byId("add-mqm").addEventListener("click", () => { addMqmRow(); markDirty(); });
  byId("previous-item").addEventListener("click", () => moveBy(-1));
  byId("next-item").addEventListener("click", () => moveBy(1));
  byId("lock-form").addEventListener("submit", lockAnnotations);
  document.addEventListener("keydown", (event) => {
    const direction = shortcutDirection(event);
    if (direction !== 0) {
      event.preventDefault();
      moveBy(direction);
    }
  });
  window.addEventListener("beforeunload", (event) => {
    if (!shouldWarnBeforeUnload(state)) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

async function start() {
  installEvents();
  try {
    const payload = await requestJson("/api/state");
    state.bundle = payload.bundle;
    state.review = payload.state;
    state.index = state.bundle.items.findIndex((item) => !state.review.latest[item.id]);
    if (state.index < 0) state.index = 0;
    updateProgress();
    renderCurrent();
  } catch (error) {
    setError(`Could not load the review queue: ${error.message}`);
    setSaveStatus("Queue unavailable", "dirty");
  }
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    allowNavigation,
    applyProgress,
    interactionState,
    hasComparisonCycle,
    labelsFor,
    lockAllowed,
    nextRevision,
    pairsFor,
    pythonLength,
    saveSettlement,
    shortcutDirection,
    shouldWarnBeforeUnload,
    validPythonSpan,
  };
}

if (typeof document !== "undefined" && typeof window !== "undefined") {
  start();
}
