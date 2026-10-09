/** Stable identities keep exercise files and answer order unchanged on revisit. */
export function stableHash(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index++) {
    hash = Math.imul(hash ^ value.charCodeAt(index), 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export function stepIdentity(step: { id?: string; cls: string; output: string }): string {
  return stableHash(step.id || `${step.cls}\n${step.output}`);
}

/** Seeded Fisher–Yates: answer positions vary by question, never by correctness. */
export function shuffledChoices(choices: readonly string[], identity: string): string[] {
  const result = [...choices];
  let seed = parseInt(stableHash(identity), 16);
  for (let index = result.length - 1; index > 0; index--) {
    seed += 0x6d2b79f5;
    let value = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    value ^= value + Math.imul(value ^ (value >>> 7), 61 | value);
    const other = Math.floor(((value ^ (value >>> 14)) >>> 0) / 4294967296 * (index + 1));
    [result[index], result[other]] = [result[other], result[index]];
  }
  return result;
}

export function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** Choice text is data, never interpolated into executable JavaScript. */
export function choiceButton(choice: string): string {
  const escaped = escapeHtml(choice);
  return `<button class="choice-btn" data-choice="${escaped}" aria-pressed="false">${escaped}</button>`;
}

export function exerciseContent(
  existing: string,
  starter: string,
  restored: string | undefined,
  stepKey: string,
  isolated: boolean
): string {
  if (isolated) {
    return existing.trim() ? existing : restored || starter;
  }
  const marker = `# --- SparkTutor 练习 ${stepKey} ---`;
  const base = existing.trim() ? existing : restored || "";
  if (base.includes(marker)) {
    return base;
  }
  // Restored work may predate markers; preserve it without adding its starter twice.
  if (starter.trim() && base.includes(starter.trim())) {
    return `${base.trimEnd()}\n\n${marker}\n`;
  }
  return `${base.trimEnd()}${base.trim() ? "\n\n" : ""}${marker}\n${starter}`;
}
