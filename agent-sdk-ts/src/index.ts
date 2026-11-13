/** EvoWanCore agent-sdk TypeScript 客户端（零运行时依赖，原生 fetch）。 */

import {
  CancelRunResponse,
  PurgeResponse,
  RunStatus,
  SdkEvent,
  SdkHttpError,
  StreamOptions,
  SubmitRunRequest,
  TERMINAL_STATES,
} from "./types.js";

export interface ClientOptions {
  /** bearer token：设置后所有请求带 Authorization: Bearer <token>。 */
  authToken?: string;
  /**
   * fetch 实现（默认全局 fetch）。连接复用说明：
   * - Node >= 18：全局 fetch 基于 undici，默认走连接池（keep-alive），
   *   无需额外配置；需要调优（keepAliveTimeout/pipelining/自定义 Agent）
   *   时，可注入绑定 dispatcher 的 fetch（如 `undici.fetch` + `setGlobalDispatcher`，
   *   或 `(url, init) => undici.fetch(url, { ...init, dispatcher })`）。
   * - 浏览器：同源连接由浏览器池化，注入 fetch 亦可接 mock。
   */
  fetch?: typeof fetch;
}

export class AgentSDKClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly authHeaders: Record<string, string>;

  constructor(baseUrl: string, opts: ClientOptions = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.fetchImpl = opts.fetch ?? fetch;
    this.authHeaders = opts.authToken
      ? { Authorization: `Bearer ${opts.authToken}` }
      : {};
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
      if (Date.now() > deadline) {  // 判定前置：挂住的请求也不能越过 deadline
        throw new Error(`waitForTerminal(${runId}) 超时 ${timeoutMs}ms`);
      }
      const run = await this.getRun(runId);
      if (TERMINAL_STATES.has(run.state)) return run;
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
      ...this.authHeaders,
    };
    if (opts.lastEventId) headers["Last-Event-ID"] = opts.lastEventId;
    // 连接建立 30s 超时（流本身长期打开，不受此限）；可被 signal 中止
    const connectSignal = opts.signal ?? undefined;
    const resp = await this.fetchImpl(
      `${this.baseUrl}/runs/${encodeURIComponent(runId)}/events/stream`,
      {
        headers,
        signal: connectSignal
          ? AbortSignal.any([connectSignal, AbortSignal.timeout(30_000)])
          : AbortSignal.timeout(30_000),
      },
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
      // CRLF 兼容：先归一为 LF（跨 chunk 的 \r|\n 在下一轮 append 后被合并处理）
      buffer = buffer.replace(/\r\n/g, "\n");
      // SSE 帧以空行分隔；逐帧解析 id:/data:/event:
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        if (frame.startsWith(":")) continue; // 心跳注释帧
        const lines = frame.split("\n");
        const dataLines: string[] = [];
        let eventName = "";
        for (const line of lines) {
          if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
          else if (line.startsWith("event:")) eventName = line.slice(6).trim();
        }
        if (eventName === "end") return; // 终态排空，流关闭
        // 多行 data 按 SSE 规范以 \n join
        const data = dataLines.join("\n");
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
    const resp = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method,
      headers: {
        ...this.authHeaders,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(30_000),  // 单请求上限（防挂死轮询）
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
