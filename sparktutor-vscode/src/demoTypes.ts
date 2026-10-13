import { DiagnosisResult } from "./types";
import { HistoryEvent, LearningReviewResult } from "./learningHistoryTypes";

export type DemoMode = "recorded" | "spark";
export type DemoScenarioId = "transfer_retry" | "transfer_first_pass";
export interface DemoSelection { mode: DemoMode; scenarioId: DemoScenarioId }
export interface DemoRunRequest extends DemoSelection { runId: string }
export interface DemoCheck { id: string; label: string; passed: boolean }
export interface DemoTask { courseId: string; lessonId: string; stepId: string; taskId: string; title: string; context: string }
export interface DemoStage {
  id: string;
  title: string;
  explanation: string;
  diagnosis: DiagnosisResult;
  review: LearningReviewResult | null;
  events: HistoryEvent[];
  checks: DemoCheck[];
}
export interface DemoReport extends DemoSelection {
  runId?: string;
  schemaVersion: 1;
  isDemo: true;
  isolated: true;
  title: string;
  provenanceLabel: string;
  disclaimer: string;
  taskA: DemoTask;
  taskB: DemoTask;
  stages: DemoStage[];
  checks: DemoCheck[];
  eventCount: number;
  startedAt: string;
  finishedAt: string;
  sparkVerified: boolean;
}
export interface DemoProgress { runId: string; id: string; title: string; index: number; total: number }
