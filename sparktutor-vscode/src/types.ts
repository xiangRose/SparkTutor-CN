/** Shared TypeScript interfaces matching Python dataclasses. */

export interface CourseMeta {
  id: string;
  title: string;
  description: string;
  lessonCount: number;
  requiresLakehouse: boolean;
  lessons: string[];
  prerequisites: string[];
}

export interface CourseProgress {
  started: boolean;
  currentLessonId?: string;
  currentLessonIdx?: number;
  lessonsCompleted?: number;
  totalLessons?: number;
  depth?: string;
  updatedAt?: string;
  lessons?: DashboardLesson[];
}

export interface StepData {
  id?: string;
  cls: string;
  depth: string;
  output: string;
  answerChoices: string | null;
  correctAnswer: string | null;
  hint: string | null;
  starterCode: string | null;
  solutionCode: string | null;
  requiresExecution: boolean;
  lessonTitle: string | null;
  estimatedMinutes: number | null;
  validation: ValidationRule[];
}

export interface ValidationRule {
  type: string;
  params: Record<string, unknown>;
}

export interface FeedbackItem {
  line: number | null;
  severity: "error" | "warning" | "info" | "success";
  message: string;
  suggestion: string | null;
  category: "bug" | "convention" | "best_practice" | null;
}

export interface EvalResult {
  passed: boolean;
  feedback: FeedbackItem[];
  encouragement: string;
  skillSignals: string[];
}

export interface ExecResult {
  exitCode: number;
  stdout: string;
  stderr: string;
  mode: string;
}

export interface LoadLessonResult {
  practiceMode?: boolean;
  step: StepData;
  currentIndex: number;
  totalSteps: number;
  lessonTitle: string;
  lessonId: string;
  restoredCode: string;
  legacyCode?: string;
  starterCode: string;
  firstStarterCode?: string;
  coursePrerequisites?: string[];
}

export interface StepResult {
  step: StepData;
  currentIndex: number;
  totalSteps: number;
  starterCode: string;
}

export interface AdvanceResult {
  finished: boolean;
  practiceMode?: boolean;
  step?: StepData;
  currentIndex?: number;
  totalSteps?: number;
  starterCode?: string;
}

export interface GoBackResult {
  atStart: boolean;
  step?: StepData;
  currentIndex?: number;
  totalSteps?: number;
  starterCode?: string;
}

export interface DiagnosisDimension {
  key: string;
  name: string;
  score: number | null;
  confidence?: string;
  evidenceLevel?: "none" | "limited" | "available";
  sampleSize?: number;
  direction: "higher_is_better" | "descriptive";
  evidenceCount: number;
  summary: string;
  metrics?: Record<string, unknown>;
  evidence?: {
    items: { courseId: string; lessonId: string; taskId: string; stepId: string; title: string;
      outcome: string; passed?: boolean; hinted?: boolean; assisted?: boolean; episodeIndex?: number }[];
    totalCount: number;
    limit: number;
  };
}

export interface RecommendedExercise {
  courseId: string;
  lessonId: string;
  stepId: string;
  lessonIndex: number;
  title: string;
  dimension: string;
  reason: string;
}

export interface DiagnosisResult {
  diagnosis: string;
  disclaimer: string;
  eventCount: number;
  eligibleEventCount: number;
  dimensions: DiagnosisDimension[];
  recommendedExercise: RecommendedExercise | null;
  recommendationReason: string;
  knowledgeComponents?: Record<string, KnowledgeComponentEvidence>;
}

export interface KnowledgeComponentEvidence {
  evaluatedTasks: number;
  latestPassedTasks: number;
}

export interface DashboardLesson {
  id: string;
  title: string;
  index: number;
  estimatedMinutes?: number | null;
  status: "not_started" | "in_progress" | "completed" | "unavailable";
  available?: boolean;
  currentStepId?: string | null;
  currentStep: number;
  totalSteps: number;
  depth: string;
}

export interface DashboardCourse {
  id: string;
  title: string;
  description: string;
  lessonCount: number;
  requiresLakehouse: boolean;
  prerequisites: string[];
  lessons: DashboardLesson[];
  progress?: { started: boolean; lessonsCompleted: number; totalLessons: number;
    inProgressLessons: number; completionPercent: number };
}

export interface DashboardResume {
  courseId: string;
  courseTitle: string;
  lessonId: string;
  lessonTitle: string;
  lessonIdx: number;
  depth: string;
  stepId?: string | null;
  currentStep: number;
  action: "resume" | "start";
}

export interface LearningDashboardResult {
  courses: DashboardCourse[];
  selectedCourseId: string;
  catalogScope: "all";
  resume: DashboardResume | null;
  diagnosis: DiagnosisResult | null;
  knowledgeComponents: Record<string, KnowledgeComponentEvidence>;
  warnings?: { section: string; message: string }[];
}

export interface OpenRecommendedExerciseResult extends LoadLessonResult {
  courseId: string;
  lessonIdx: number;
  depth: string;
}
