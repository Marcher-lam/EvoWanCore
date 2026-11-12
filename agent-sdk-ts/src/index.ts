/** EvoWanCore agent-sdk TypeScript 客户端（零运行时依赖，原生 fetch）。 */

import {
  CancelRunResponse,
  PurgeResponse,
  RunStatus,
  SdkEvent,
  SdkHttpError,
  StreamOptions,
  SubmitRunRequest,
} from "./types.js";
import { TERMINAL_STATES } from "./types.js";

export class AgentSDKClient {
  private readonly baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  /** 提交任务 → 立即返回 QUEUED（提交-轮询模型）。 */
  async submitRun(req: SubmitRunRequest): Promise<RunStatus> {
    return this.json("POST", "/runs", req, 201);
  }

  /** 查询 run；404 抛 SdkHttpError。 */
  async getRun(runId: string): Promise<RunStatus> {
    return this.json("GET", `/runs/${encodeURIComponent(runId)}`);
  }

  /** 轮询直到终态（默认 30s 上限）。 */
  async waitForTerminal(
    runId: string,
    opts: { timeoutMs?: number; intervalMs?: number } = {},
  ): Promise<RunStatus> {
    const timeoutMs = opts.timeoutMs ?? 30_000;
    const intervalMs = opts.intervalMs ?? 100;
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const run = await this.getRun(runId);
      if (TERMINAL_STATES.has(run.state)) return run;
      if (Date.now() > deadline) {
        throw new Error(`waitForTerminal(${runId}) 超时 ${timeoutMs}ms`);
      }
      await sleep(intervalMs);
    }
  }

  /** 全量事件列表。 */
  async listEvents(runId: string): Promise<SdkEvent[]> {
    const body = await this.json<{ events: SdkEvent[]; count: number }>(
      "GET", `/runs/${encodeURIComponent(runId)}/events`,
    );
    return body.events;
  }

  /** 取消 run（幂等；cancelled 如实反映）。 */
  async cancelRun(runId: string): Promise<CancelRunResponse> {
    return this.json("POST", `/runs/${encodeURIComponent(runId)}/cancel`, {});
  }

  /** 恢复非终态 run（注入 image_urls 协议；202 + resuming）。 */
  async resumeRun(
    runId: string,
    imageUrls: Record<string, string> = {},
  ): Promise<RunStatus> {
    return this.json(
      "POST", `/runs/${encodeURIComponent(runId)}/resume`,
      { image_urls: imageUrls }, 202,
    );
  }

  /** 物理清除记忆 + 审计（需服务端配置 memory 组件）。 */
  async purgeMemory(
    namespace: string,
    recordId: string,
    reason = "ts-sdk",
  ): Promise<PurgeResponse> {
    const qs = `?reason=${encodeURIComponent(reason)}`;
    return this.json(
      "DELETE",
      `/memory/${encodeURIComponent(namespace)}/${encodeURIComponent(recordId)}${qs}`,
    );
  }

  /**
   * 订阅 SSE 事件流：每事件回调一次；服务端 event: end 后 resolve。
   * 支持断线游标（lastEventId）与中止（signal）。
   */
  async streamEvents(
    runId: string,
    onEvent: (event: SdkEvent) => void,
    opts: StreamOptions = {},
  ): Promise<void> {
    const headers: Record<string, string> = {
      Accept: "text/event-stream",
    };
    if (opts.lastEventId) headers["Last-Event-ID"] = opts.lastEventId;
    const resp = await fetch(
      `${this.baseUrl}/runs/${encodeURIComponent(runId)}/events/stream`,
      { headers, signal: opts.signal },
    );
    if (!resp.ok || !resp.body) {
      throw new SdkHttpError(resp.status, this.baseUrl, await resp.text());
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      // SSE 帧以空行分隔；逐帧解析 id:/data:/event:
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        if (frame.startsWith(":")) continue; // 心跳注释帧
        const lines = frame.split("\n");
        let data = "";
        let eventName = "";
        for (const line of lines) {
          if (line.startsWith("data:")) data += line.slice(5).trim();
          else if (line.startsWith("event:")) eventName = line.slice(6).trim();
        }
        if (eventName === "end") return; // 终态排空，流关闭
        if (data) onEvent(JSON.parse(data) as SdkEvent);
      }
    }
  }

  // ---------- 内部 ----------

  private async json<T>(
    method: string,
    path: string,
    body?: unknown,
    expect = 200,
  ): Promise<T> {
    const resp = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (resp.status !== expect) {
      throw new SdkHttpError(resp.status, path, await resp.text());
    }
    return (await resp.json()) as T;
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export * from "./types.js";
