export type EnvironmentAction = "pythonSettings" | "modeSettings" | "aiSettings" | "configureCoach" | "checkAi" | "reload" | "diagnostics";
export type EnvironmentStatus = "available" | "missing" | "warning" | "unverified";
export interface EnvironmentCheck {
  id: string;
  title: string;
  status: EnvironmentStatus;
  summary: string;
  details: string[];
  nextSteps: string[];
}
export interface EnvironmentReport {
  schemaVersion: 1;
  staticOnly: true;
  sparkVerified: false;
  checks: EnvironmentCheck[];
  execution: { configuredMode: string; localPrerequisites: boolean; summary: string; nextSteps: string[] };
  capabilities: { id: string; title: string; availability: "available" | "needs_setup" | "unverified"; detail: string }[];
  backend?: { available: boolean; pythonPath?: string; summary?: string };
  ai?: { provider: string; configured: boolean; summary: string };
}

/** Explicit allow-list: a webview cannot send a command, path, or credentials. */
export const ENVIRONMENT_ACTIONS: readonly EnvironmentAction[] = [
  "pythonSettings", "modeSettings", "aiSettings", "configureCoach", "checkAi", "reload", "diagnostics",
];

export function isEnvironmentReport(value: unknown): value is EnvironmentReport {
  if (!value || typeof value !== "object") { return false; }
  const r = value as EnvironmentReport;
  const strings = (items: unknown): items is string[] => Array.isArray(items) && items.every((item) => typeof item === "string");
  return r.schemaVersion === 1 && r.staticOnly === true && r.sparkVerified === false &&
    Array.isArray(r.checks) && r.checks.every((item) => item && typeof item.id === "string" &&
      typeof item.title === "string" && ["available", "missing", "warning", "unverified"].includes(item.status) &&
      typeof item.summary === "string" && strings(item.details) && strings(item.nextSteps)) &&
    Boolean(r.execution && typeof r.execution.configuredMode === "string" && typeof r.execution.localPrerequisites === "boolean" &&
      typeof r.execution.summary === "string" && strings(r.execution.nextSteps)) &&
    Array.isArray(r.capabilities) && r.capabilities.every((item) => item && typeof item.id === "string" && typeof item.title === "string" &&
      ["available", "needs_setup", "unverified"].includes(item.availability) && typeof item.detail === "string") &&
    (!r.backend || (typeof r.backend.available === "boolean" && (r.backend.pythonPath === undefined || typeof r.backend.pythonPath === "string") &&
      (r.backend.summary === undefined || typeof r.backend.summary === "string"))) &&
    (!r.ai || (typeof r.ai.provider === "string" && typeof r.ai.configured === "boolean" && typeof r.ai.summary === "string"));
}
