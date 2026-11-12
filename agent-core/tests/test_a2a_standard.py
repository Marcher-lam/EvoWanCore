"""V8-T1 A2A Google spec 核心子集：Agent Card + JSON-RPC message/send。"""


import aiohttp
from agent_core.transport import A2AEnvelope


def _card(**overrides):
    from agent_core.a2a_standard import AgentCard

    base = {
        "name": "doc-worker",
        "description": "文档抽取 worker",
        "url": "http://127.0.0.1:1/",
        "version": "1.0.0",
        "skills": [{"id": "extract", "name": "字段抽取", "tags": ["doc"]}],
    }
    base.update(overrides)
    return AgentCard(**base)


async def test_agent_card_served() -> None:
    """GET /.well-known/agent-card.json → 标准 Card。"""
    from agent_core.a2a_standard import A2AStandardEndpoint

    async def handler(env: A2AEnvelope) -> A2AEnvelope | None:
        return None

    ep = A2AStandardEndpoint(_card(), handler, port=0)
    await ep.start()
    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(
                f"http://127.0.0.1:{ep.port}/.well-known/agent-card.json"
            ) as resp:
                assert resp.status == 200
                card = await resp.json()
        assert card["name"] == "doc-worker"
        # wire = camelCase（A2A spec 互操作面，终评审 H1）
        assert card["protocolVersion"].startswith("0.2")
        assert card["preferredTransport"] == "JSONRPC"
        assert card["skills"][0]["id"] == "extract"
        assert card["capabilities"]["pushNotifications"] is False
        assert card["capabilities"]["streaming"] is False  # Ruling: 不做 streaming
    finally:
        await ep.stop()


async def test_message_send_roundtrip() -> None:
    """A2AStandardClient.send_text → JSON-RPC message/send → handler → 回复文本。"""
    from agent_core.a2a_standard import A2AStandardClient, A2AStandardEndpoint

    received: list[A2AEnvelope] = []

    async def handler(env: A2AEnvelope) -> A2AEnvelope | None:
        received.append(env)
        return A2AEnvelope(
            from_agent=env.to_agent, to_agent=env.from_agent,
            message_type="reply",
            payload={"text": "fields extracted: 3"},
            reply_to=env.message_id,
        )

    ep = A2AStandardEndpoint(_card(), handler, port=0)
    await ep.start()
    try:
        client = A2AStandardClient(f"http://127.0.0.1:{ep.port}")
        reply = await client.send_text("抽取发票字段")
        assert reply == "fields extracted: 3"
        # handler 侧收到的是标准信封：text part 进 payload.text
        assert len(received) == 1
        assert received[0].payload["text"] == "抽取发票字段"
        assert received[0].message_type == "task_request"
        assert received[0].from_agent == "a2a:remote", "远端身份稳定（不随 messageId 漂移）"
        assert received[0].payload["message_id"], "远端 messageId 入 payload 对账"
    finally:
        await ep.stop()


async def test_jsonrpc_unknown_method() -> None:
    """非 message/send method → JSON-RPC -32601。"""
    from agent_core.a2a_standard import A2AStandardEndpoint

    async def handler(env):
        return None

    ep = A2AStandardEndpoint(_card(), handler, port=0)
    await ep.start()
    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"http://127.0.0.1:{ep.port}/",
                json={"jsonrpc": "2.0", "id": "1", "method": "tasks/get",
                      "params": {}},
            ) as resp:
                assert resp.status == 200, "JSON-RPC 错误走 200 + error 体"
                body = await resp.json()
        assert body["error"]["code"] == -32601
        assert body["id"] == "1"
    finally:
        await ep.stop()


async def test_auth_token_enforced() -> None:
    """auth_token 设置 → 无凭证 401；A2AStandardClient 带凭证闭环。"""
    from agent_core.a2a_standard import A2AStandardClient, A2AStandardEndpoint

    async def handler(env: A2AEnvelope) -> A2AEnvelope | None:
        return A2AEnvelope(
            from_agent=env.to_agent, to_agent=env.from_agent,
            message_type="reply", payload={"text": "ok"},
        )

    ep = A2AStandardEndpoint(_card(), handler, port=0, auth_token="a2akey")
    await ep.start()
    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"http://127.0.0.1:{ep.port}/",
                json={"jsonrpc": "2.0", "id": "1", "method": "message/send",
                      "params": {}},
            ) as resp:
                assert resp.status == 401
        reply = await A2AStandardClient(
            f"http://127.0.0.1:{ep.port}", auth_token="a2akey"
        ).send_text("hi")
        assert reply == "ok"
    finally:
        await ep.stop()


async def test_bad_json_parse_error() -> None:
    """非法 JSON → JSON-RPC -32700。"""
    from agent_core.a2a_standard import A2AStandardEndpoint

    async def handler(env):
        return None

    ep = A2AStandardEndpoint(_card(), handler, port=0)
    await ep.start()
    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"http://127.0.0.1:{ep.port}/", data="{oops",
                headers={"Content-Type": "application/json"},
            ) as resp:
                body = await resp.json()
        assert body["error"]["code"] == -32700
    finally:
        await ep.stop()
