/** Counts editor changes without retaining or transmitting source text or file paths. */
export interface EditContext {
  courseId: string;
  lessonId: string;
  taskId: string;
  documentUri: string;
  /** Present only for a short question in a shared exercise buffer. */
  blockKey?: string;
}

interface DocumentChange {
  document: { uri: { toString(): string }; version: number; getText(): string };
  contentChanges: readonly { text: string; rangeLength: number; rangeOffset: number }[];
}

interface BlockRange {
  start: number;
  end: number;
  last: boolean;
  markerIds: string;
  boundaries: { start: number; end: number }[];
}

/** Only offsets survive this scan; neither source text nor marker contents are retained. */
function findBlock(text: string, key: string): BlockRange | null {
  const markers = [...text.matchAll(/^# --- SparkTutor 练习 ([a-zA-Z0-9_-]+) ---\r?$/gm)];
  const matching = markers.filter((marker) => marker[1] === key);
  if (matching.length !== 1) { return null; }
  const marker = matching[0];
  const index = markers.indexOf(marker);
  const start = marker.index! + marker[0].length + (text[marker.index! + marker[0].length] === "\n" ? 1 : 0);
  return { start, end: markers[index + 1]?.index ?? text.length, last: index === markers.length - 1,
    markerIds: markers.map((item) => item[1]).join(","),
    boundaries: markers.map((item) => ({ start: item.index!, end: item.index! + item[0].length })) };
}

interface Burst {
  context: EditContext;
  changeCount: number;
  addedChars: number;
  removedChars: number;
  documentVersion: number;
  startedAt: number;
  lastAt: number;
}

interface EventBridge {
  call<T = unknown>(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<T>;
}

const RECORD_TIMEOUT_MS = 1500;

export class LearningEventTracker {
  private context: EditContext | null = null;
  private blockRange: BlockRange | null = null;
  private blockInvalid = false;
  private paused = true;
  private stopping = false;
  private pending: Burst | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private readonly inFlight = new Set<Promise<void>>();
  private navigation: Promise<unknown> = Promise.resolve();
  private endPromise: Promise<void> | undefined;

  constructor(private readonly bridge: EventBridge, private readonly reportFailure: (message: string) => void = () => {}) {}

  /** Called only after programmatic starter/restored code has been installed while paused. */
  bind(context: EditContext | null, documentText = ""): void {
    this.context = context ? { ...context } : null;
    this.blockRange = context?.blockKey ? findBlock(documentText, context.blockKey) : null;
    this.blockInvalid = Boolean(context?.blockKey && !this.blockRange);
  }

  observe(event: DocumentChange): void {
    if (this.stopping || !this.context || !event.contentChanges.length ||
      event.document.uri.toString() !== this.context.documentUri) { return; }
    if (this.context.blockKey) {
      if (this.blockInvalid) { return; }
      const before = this.blockRange;
      const after = findBlock(event.document.getText(), this.context.blockKey);
      if (!before || !after || before.markerIds !== after.markerIds || event.contentChanges.some((change) =>
        before.boundaries.some((marker) => change.rangeOffset <= marker.end &&
          change.rangeOffset + change.rangeLength >= marker.start))) {
        // Once a boundary changes, do not silently absorb a neighbouring answer.
        // Only opening the task again can establish a trustworthy scope.
        this.blockInvalid = true;
        this.blockRange = null;
        return;
      }
      this.blockRange = after;
      // Change offsets refer to the old document. Reject ambiguous edits rather than
      // attributing changes to a previous answer or to a deleted/replaced boundary.
      if (event.contentChanges.some((change) =>
        change.rangeOffset < before.start || change.rangeOffset + change.rangeLength > before.end ||
        (!before.last && change.rangeOffset === before.end) ||
        /^# --- SparkTutor 练习 /m.test(change.text))) { return; }
    }
    if (this.paused) { return; }
    const now = Date.now();
    if (!this.pending) {
      this.pending = { context: { ...this.context }, changeCount: 0, addedChars: 0, removedChars: 0,
        documentVersion: event.document.version, startedAt: now, lastAt: now };
    }
    for (const change of event.contentChanges) {
      this.pending.changeCount++;
      // VS Code rangeLength and JavaScript length both count UTF-16 code units.
      this.pending.addedChars += change.text.length;
      this.pending.removedChars += change.rangeLength;
    }
    this.pending.documentVersion = event.document.version;
    this.pending.lastAt = now;
    if (this.timer) { clearTimeout(this.timer); }
    this.timer = setTimeout(() => { void this.flush(); }, 500);
  }

  /** Await all already-sent edits as well as the last debounce burst. */
  async flush(): Promise<void> {
    if (this.timer) { clearTimeout(this.timer); this.timer = undefined; }
    const burst = this.pending;
    this.pending = null;
    if (burst) {
      const { courseId, lessonId, taskId } = burst.context;
      const request = this.bridge.call<{ recorded: boolean }>("recordLearningEvent", {
        eventType: "code_edit", courseId, lessonId, taskId,
        data: { changeCount: burst.changeCount, addedChars: burst.addedChars,
          removedChars: burst.removedChars, documentVersion: burst.documentVersion,
          burstDurationMs: Math.max(0, burst.lastAt - burst.startedAt) },
      }, RECORD_TIMEOUT_MS).then((result) => {
        if (!result.recorded) { this.reportFailure("本次编辑计数未保存：题目已切换或当前任务不接受编辑记录。"); }
      }, () => {
        this.reportFailure("本次编辑计数未能确认保存，学习操作仍可继续。");
      });
      this.inFlight.add(request);
      void request.finally(() => this.inFlight.delete(request));
    }
    await Promise.all([...this.inFlight]);
  }

  /** Serialize navigation, flush the old identity, and suppress programmatic edits. */
  transition<T>(action: () => Promise<T>): Promise<T> {
    const transition = this.navigation.then(async () => {
      if (this.stopping) { throw new Error("扩展正在关闭，请稍后重新打开课程。"); }
      this.paused = true;
      await this.flush();
      try { return await action(); }
      finally { this.paused = this.stopping; }
    });
    this.navigation = transition.catch(() => {});
    return transition;
  }

  /** The bridge enforces a short timeout on each request; shutdown is idempotent. */
  endSession(): Promise<void> {
    if (!this.endPromise) {
      this.stopping = true;
      this.paused = true;
      this.endPromise = (async () => {
        await this.flush();
        try {
          await this.bridge.call("recordLearningEvent", {
            eventType: "session_end", data: { reason: "extension_deactivated" },
          }, RECORD_TIMEOUT_MS);
        } catch {
          this.reportFailure("会话结束记录未能确认保存。");
        }
      })();
    }
    return this.endPromise;
  }
}

/** Both VS Code subscription disposal and deactivate use this same awaited shutdown. */
export function createTrackerShutdown(tracker: LearningEventTracker, dispose: () => void): () => Promise<void> {
  let shutdown: Promise<void> | undefined;
  return () => {
    shutdown ??= tracker.endSession().finally(dispose);
    return shutdown;
  };
}
