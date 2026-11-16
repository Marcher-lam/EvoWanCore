/** EvoWanCore agent-sdk HTTP API 契约类型（与 Python 端 models.py 对齐）。 */

export interface SubmitRunRequest {
  task_id: string;
  kind: string;
  goal?: Record<string, unknown>;
  input_schema?: Record<string, unknown>;
  constraints?: Record<string, unknown>;
  acceptance_criteria?: Array<Record<string, unknown>>;
  agent_id?: string;
}

/** run 终态集合（非终态为 queued/running/resuming）。 */
export type RunState =
  | "queued"
  | "running"
  | "resuming"
  | "succeeded"
  | "failed"
  | "cancelled"
  | (string & {});

export const TERMINAL_STATES: ReadonlySet<string> = new Set([
  "succeeded",
  "failed",
  "cancelled",
]);

export interface RunStatus {
  run_id: string;
  state: RunState;
  task_id: string;
}

export interface SdkEvent {
  id: string;
  type: string;
  ts: string;
  subject_type: string;
  subject_id: string;
  payload: Record<string, unknown>;
}

export interface CancelRunResponse {
  run_id: string;
  cancelled: boolean;
}

export interface PurgeResponse {
  record_id: string;
  deleted_revisions: number;
  reason: string;
}

/** SSE 流订阅选项。 */
export interface StreamOptions {
  /** 断线重连游标：只收该事件 id 之后的事件。 */
  lastEventId?: string;
  /** 中止信号（AbortController）。 */
  signal?: AbortSignal;
}

/** SDK API 错误（非 2xx 响应）。 */
export class SdkHttpError extends Error {
  constructor(
    readonly status: number,
    readonly url: string,
    readonly body: string,
  ) {
    super(`agent-sdk ${status} at ${url}: ${body.slice(0, 200)}`);
    this.name = "SdkHttpError";
  }
}

/** MRAG 查询模式：answer=VLM 带引用生成；retrieve=仅检索。 */
export type MragMode = "answer" | "retrieve";

export interface MragHit {
  content: string;
  doc: string;
  page: number;
  score: number;
  page_image: string;
}

export interface MragCitation {
  doc: string;
  page: number;
}

export interface MragQueryResponse {
  answer: string;
  citations: MragCitation[];
  hits: MragHit[];
}
