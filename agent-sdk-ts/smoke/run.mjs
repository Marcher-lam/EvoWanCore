/**
 * TS SDK 冒烟 e2e：起 Python AgentSDKServer → submit → 轮询终态 →
 * SSE 实时收事件 → events 全量 → cancel 幂等。
 * 用法：node smoke/run.mjs（在 agent-sdk-ts/ 下执行）
 */
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

function fail(msg) {
  console.error(`SMOKE FAIL: ${msg}`);
  process.exitCode = 1;
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

async function main() {
  const require = createRequire(import.meta.url);
  const { AgentSDKClient, SdkHttpError } = require("../dist/index.js");

  // 1. 起 Python server：cwd=uv workspace 根（解析 workspace），脚本用绝对路径
  // smoke/run.mjs → 上两级 = 仓库根（fileURLToPath 防 URL 百分号编码）
  const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
  const serverPy = fileURLToPath(new URL("./server.py", import.meta.url));
  const child = spawn(
    "uv", ["run", "python", serverPy],
    { cwd: repoRoot, stdio: ["ignore", "pipe", "pipe"] },
  );
  let baseUrl = null;
  const stderr = [];
  child.stderr.on("data", (d) => stderr.push(String(d)));

  try {
    baseUrl = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`server 未就绪: ${stderr.join("")}`)), 60_000);
      child.stdout.on("data", (d) => {
        const m = String(d).match(/READY (http:\/\/\S+)/);
        if (m) { clearTimeout(timer); resolve(m[1]); }
      });
      child.on("exit", (code) => reject(new Error(`server 提前退出 code=${code}: ${stderr.join("")}`)));
    });
    console.log(`server ready: ${baseUrl}`);

    const client = new AgentSDKClient(baseUrl);

    // 2. submit → queued
    const submitted = await client.submitRun({
      task_id: "ts-smoke-1", kind: "test", goal: { what: "noop" },
      acceptance_criteria: [{ check: "done" }],
    });
    assert(submitted.state === "queued", `期望 queued, 实际 ${submitted.state}`);
    console.log(`submitted: ${submitted.run_id} (${submitted.state})`);

    // 3. 轮询终态
    const final = await client.waitForTerminal(submitted.run_id, { timeoutMs: 15_000 });
    assert(final.state === "succeeded", `期望 succeeded, 实际 ${final.state}`);
    console.log(`terminal: ${final.state}`);

    // 4. SSE 流（重放模式）：≥3 事件 + 正常结束
    const events = [];
    await client.streamEvents(submitted.run_id, (e) => events.push(e));
    assert(events.length >= 3, `SSE 事件 ${events.length} < 3`);
    const types = events.map((e) => e.type);
    assert(types.includes("RunCreated") && types.includes("RunVerified"),
      `SSE 缺关键事件: ${types.join(",")}`);
    // 5. Last-Event-ID 游标：只收断点后事件
    const cursorEvents = [];
    await client.streamEvents(submitted.run_id, (e) => cursorEvents.push(e), {
      lastEventId: events[1].id,
    });
    assert(
      cursorEvents.map((e) => e.id).join() === events.slice(2).map((e) => e.id).join(),
      "Last-Event-ID 游标语义不符",
    );
    console.log(`sse: ${events.length} events + cursor replay ok`);

    // 6. events 全量列表
    const listed = await client.listEvents(submitted.run_id);
    assert(listed.length === events.length, "listEvents 与 SSE 数量不一致");

    // 7. cancel 幂等（已终态 → cancelled=false 如实）
    const cancelled = await client.cancelRun(submitted.run_id);
    assert(cancelled.cancelled === false, "终态 run 的 cancel 应回 cancelled=false");
    console.log("cancel idempotent: ok");

    // 8. 404 错误面
    let threw = false;
    try {
      await client.getRun("nonexistent");
    } catch (e) {
      threw = e instanceof SdkHttpError && e.status === 404;
    }
    assert(threw, "getRun(不存在) 应抛 404 SdkHttpError");
    console.log("404 error surface: ok");

    console.log("SMOKE PASS ✅");
  } catch (e) {
    fail(e.message);
  } finally {
    // 温和终止 → 等退出（3s）→ SIGKILL 兜底（不留僵尸/不挂 node 事件循环）
    child.kill("SIGTERM");
    const exited = new Promise((resolve) => child.on("exit", resolve));
    const timedOut = await Promise.race([
      exited.then(() => true),
      sleep(3000).then(() => false),
    ]);
    if (!timedOut) child.kill("SIGKILL");
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

await main();
