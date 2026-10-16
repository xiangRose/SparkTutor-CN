import { ReviewDimension } from "./learningHistoryTypes";

export interface CoachTask { courseId: string; lessonId: string; stepId: string; taskId: string; title: string }
export interface CoachEvidence { id: string; title: string; description: string }
export interface CoachState {
  available: boolean;
  reason: string;
  task: CoachTask | null;
  lastLevel: number;
  maxLevel: number;
  evidence: CoachEvidence[];
  dimensions: ReviewDimension[];
  answerViewed: boolean;
  exerciseOptions: CoachTask[];
}
export interface CoachPrompt {
  requestId: string;
  messages: { role: string; content: string }[];
  level: number;
  task: CoachTask;
  evidence: CoachEvidence[];
  dimensions: ReviewDimension[];
}
export interface CoachMessage {
  messageId: string;
  requestId: string;
  level: number;
  task: CoachTask;
  hypothesisId: string;
  hypothesis: string;
  question: string;
  guidance: string;
  reflection: string;
  evidence: CoachEvidence[];
  probe: { values: number[] } | null;
  answerViewed: boolean;
  disclaimer: string;
}
export interface CoachProbe {
  verified: true;
  engine: "python-reference";
  values: number[];
  rowCount: number;
  quantitySum: number;
  disclaimer: string;
}
export interface CoachAck { recorded: boolean; reason?: string; lastLevel?: number; maxLevel?: number }

export function sameCoachTask(left: CoachTask | null, right: CoachTask | null): boolean {
  return Boolean(left && right && left.courseId === right.courseId && left.lessonId === right.lessonId &&
    left.stepId === right.stepId && left.taskId === right.taskId);
}
