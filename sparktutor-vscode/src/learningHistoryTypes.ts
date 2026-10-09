/** Read-only history contracts. Assessment eligibility and snapshots belong to the backend. */
export type HistoryWindow = "all" | "7d" | "30d";

export interface HistoryCourse {
  id: string;
  title: string;
  available: boolean;
  lessons: { id: string; title: string; available: boolean }[];
}

export interface HistoryTaskRef { courseId: string; lessonId: string; taskId: string }
export interface HistoryPage { offset: number; limit: number; total: number; hasMore: boolean; snapshotEventId: string }
export interface HistoryQuery { courseId?: string; window: HistoryWindow; offset: number; limit: number; snapshotEventId?: string; windowEnd?: string }
export interface TaskHistoryQuery extends HistoryTaskRef { offset: number; limit: number; snapshotEventId?: string }
export interface LearningReviewQuery extends HistoryTaskRef { eventId: string; scopeCourseId: string; snapshotEventId?: string }
export interface LearningTrendQuery { courseId: string; window: HistoryWindow; snapshotEventId?: string; windowEnd?: string }

export interface HistoryEvent {
  eventId: string;
  eventType: string;
  timestamp: string;
  attemptNumber: number;
  passed: boolean | null;
  rawPassed?: boolean | null;
  assessmentSource: string;
  mode: string;
  category: "assessment" | "debug_failure" | "answer_view" | "behavior" | "excluded";
  reason: string;
  answerInfluenced: boolean;
  reviewable: boolean;
  hintUsed: boolean;
  available: boolean | null;
  errorTypes: string[];
  exitCode?: number;
  editCounts?: { changeCount: number; addedChars: number; removedChars: number; documentVersion?: number; burstDurationMs?: number };
}

export interface HistoryTask extends HistoryTaskRef {
  stepId: string;
  title: string;
  courseTitle: string;
  lessonTitle: string;
  available: boolean;
  taskType: string;
  lastActivityAt: string;
  counts: { submissions: number; eligibleAssessments: number; hints: number; answers: number; runs: number; edits: number };
  latestSubmission: HistoryEvent | null;
  latestAssessment: HistoryEvent | null;
  answerViewed: boolean;
}

export interface LearningHistoryResult extends HistoryPage {
  courses: HistoryCourse[];
  courseId: string;
  window: HistoryWindow;
  windowStart: string | null;
  windowEnd: string;
  tasks: HistoryTask[];
  dataQuality: Record<string, unknown>;
  disclaimer: string;
  warnings?: string[];
}

export interface TaskHistoryResult extends HistoryPage {
  task: HistoryTask;
  events: HistoryEvent[];
  dataQuality: Record<string, unknown>;
  disclaimer: string;
}

export interface ReviewDimension {
  key: string;
  name: string;
  score: number | null;
  sampleSize: number;
  direction: "descriptive" | "higher_is_better";
  numerator: number;
  denominator: number;
  metrics: Record<string, unknown>;
}

export interface ReviewChange {
  key: string;
  name: string;
  beforeScore: number | null;
  afterScore: number | null;
  delta: number | null;
  numeratorDelta: number;
  denominatorDelta: number;
  sampleDelta: number;
  explanation: string;
}

export interface LearningReviewResult {
  task: HistoryTask;
  event: HistoryEvent;
  before: { dimensions: ReviewDimension[] };
  after: { dimensions: ReviewDimension[] };
  changes: ReviewChange[];
  reason: string;
  disclaimer: string;
  dataQuality: Record<string, unknown>;
}

export interface LearningTrendPoint extends HistoryTaskRef {
  eventId: string;
  timestamp: string;
  title: string;
  type: string;
  eventLabel: string;
  dimensions: Omit<ReviewDimension, "metrics">[];
}

export interface LearningTrendResult {
  courseId: string;
  window: HistoryWindow;
  snapshotEventId: string;
  windowEnd: string;
  total: number;
  limit: number;
  hasMore: boolean;
  points: LearningTrendPoint[];
  disclaimer: string;
  dataQuality: Record<string, unknown>;
}
