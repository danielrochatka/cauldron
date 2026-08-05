/**
 * Pure functions for rendering module state in the detail panel.
 * Exported for unit testing without a full DOM environment.
 */

export function escHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function escapeAttr(s) {
  return String(s ?? "").replace(/"/g, "&quot;");
}

/**
 * Returns the pending-change warning string (includes restart note),
 * or "" when there is no pending change.
 *
 * pending_restart=true  + configured_enabled=false → "Pending disable — restart required"
 * pending_restart=true  + configured_enabled=true  → "Pending enable — restart required"
 * pending_restart=false → ""
 */
export function pendingWarning(n) {
  if (!n.pending_restart) return "";
  return n.configured_enabled
    ? "Pending enable — restart required"
    : "Pending disable — restart required";
}

/**
 * Returns HTML for the state <dl> rows shown in the detail panel.
 * Separate rows for desired, loaded, active, and (when present) pending change.
 */
export function buildStateRows(n) {
  const desiredLabel = n.configured_enabled ? "Enabled" : "Disabled";
  const loadedLabel  = n.enabled  ? "Enabled" : "Disabled";
  const activeLabel  = n.active   ? "Active"  : "Inactive";
  const warning = pendingWarning(n);
  return (
    `<dt>Desired state</dt><dd>${escHtml(desiredLabel)}</dd>` +
    `<dt>Loaded state</dt><dd>${escHtml(loadedLabel)}</dd>` +
    `<dt>Active</dt><dd>${escHtml(activeLabel)}</dd>` +
    (warning
      ? `<dt>Pending change</dt><dd class="state-pending-warning">${escHtml(warning)}</dd>`
      : "")
  );
}

/**
 * Returns HTML for the detail-panel action footer.
 *
 * Logic:
 *   No pending change (pending_restart=false):
 *     configured_enabled=true  → "Disable"           action=disable
 *     configured_enabled=false → "Enable"            action=enable
 *   Pending change (pending_restart=true):
 *     configured_enabled=false → "Undo pending disable"  action=enable
 *     configured_enabled=true  → "Undo pending enable"   action=disable
 */
export function buildActionHtml(n, previewUrl) {
  let action, label, btnClass;

  if (n.pending_restart) {
    if (!n.configured_enabled) {
      action   = "enable";
      label    = "Undo pending disable";
      btnClass = "cui-btn cui-btn-outline";
    } else {
      action   = "disable";
      label    = "Undo pending enable";
      btnClass = "cui-btn cui-btn-outline";
    }
  } else {
    if (n.configured_enabled) {
      action   = "disable";
      label    = "Disable";
      btnClass = "cui-btn cui-btn-outline";
    } else {
      action   = "enable";
      label    = "Enable";
      btnClass = "cui-btn cui-btn-primary";
    }
  }

  const warning = pendingWarning(n);

  return (
    `<div class="detail-actions">` +
    `<button class="${escapeAttr(btnClass)}" data-action="${escapeAttr(action)}" ` +
    `data-slug="${escapeAttr(n.slug)}" data-preview-url="${escapeAttr(previewUrl || "")}">` +
    `${escHtml(label)}</button>` +
    (warning
      ? `<span class="restart-warning" role="status">${escHtml(warning)}</span>`
      : (n.requires_restart
          ? `<span class="restart-warning">Requires restart</span>`
          : "")) +
    `</div>`
  );
}
