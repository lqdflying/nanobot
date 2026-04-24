# WebSocket Channel

The WebSocket channel (`nanobot/channels/websocket.py`) runs a **WebSocket server embedded in nanobot**. Clients connect to nanobot over WebSocket, send text or JSON messages, and receive responses — nanobot acts as the server rather than a client.

## Overview

```mermaid
flowchart LR
    subgraph Nanobot["nanobot (WebSocket Server)"]
        WS["WebSocketServerChannel"]
        MB["MessageBus"]
        AL["AgentLoop"]
    end

    Client1["Client A"] -->|"ws://host:port/path?client_id=A&token=..."| WS
    Client2["Client B"] -->|"ws://host:port/path?client_id=B&token=..."| WS
    Client3["Client C"] -->|"ws://host:port/path?client_id=C&token=..."| WS

    WS -->|"_handle_message()"| MB
    MB -->|"publish_inbound"| AL
    AL -->|"publish_outbound"| MB
    MB -->|"OutboundMessage"| WS
    WS -->|"send / send_delta"| Client1
    WS -->|"send / send_delta"| Client2
    WS -->|"send / send_delta"| Client3
```

Each connected client is assigned a unique `chat_id` (UUID) on connection. The `client_id` query parameter is used for `allow_from` authorization.

## Connection Lifecycle

```mermaid
sequenceDiagram
    participant C as Client
    participant WS as WebSocketServerChannel
    participant MB as MessageBus
    participant AL as AgentLoop

    C->>WS: TCP connect + WS upgrade
    WS->>WS: process_request: auth check<br/>(allow_from + token validation)
    alt Forbidden / Unauthorized
        WS-->>C: HTTP 403/401
    else OK
        WS->>C: 101 Switching Protocols
        C->>WS: {"event": "ready", "chat_id": "...", "client_id": "..."}
        Note over WS: Register chat_id → connection mapping
    end

    loop Messages
        C->>WS: text/JSON frame (content or text or message)
        WS->>WS: _parse_inbound_payload()
        WS->>WS: _handle_message(sender_id=client_id,<br/>chat_id=chat_id, content=...)
        WS->>MB: publish_inbound(InboundMessage)
        MB->>AL: process_direct()
        AL-->>MB: response
        MB->>WS: OutboundMessage
        WS->>C: {"event": "message", "text": "..."}
    end

    C->>WS: connection close
    WS->>WS: _connections.pop(chat_id)
```

Steps:

1. **TCP connect + WS upgrade** — client initiates a WebSocket handshake
2. **Auth check** (`process_request`):
   - `allow_from` check against `client_id` query param
   - Token validation (static `token` or issued token)
   - HTTP token issue endpoint (`token_issue_path`) for short-lived tokens
3. **Ready** — server sends `{"event": "ready", "chat_id": "...", "client_id": "..."}`; only then is the connection registered
4. **Message loop** — client sends frames; `_parse_inbound_payload()` extracts text from plain strings or JSON `content`/`text`/`message` fields
5. **Disconnect** — `chat_id` is removed from `_connections`

## Message Format

### Inbound (Client → Nanobot)

Plain text string **or** JSON object:

```json
// Plain text
"hello"

 // JSON object — "content", "text", or "message" field
{
  "content": "hello"
}
```

Any other JSON structure is ignored; non-UTF-8 binary frames are dropped with a warning.

### Outbound (Nanobot → Client)

Standard message:

```json
{
  "event": "message",
  "text": "Hello! How can I help you?",
  "media": [],
  "reply_to": "msg_id"
}
```

Streaming delta:

```json
{
  "event": "delta",
  "text": "Hello",
  "stream_id": "abc123"
}
```

Stream end:

```json
{
  "event": "stream_end",
  "stream_id": "abc123"
}
```

## handle_inbound

`_handle_inbound()` → `_handle_message()` → `bus.publish_inbound(InboundMessage(...))`

```mermaid
flowchart TD
    RAW["raw frame from client"] --> PARSE["_parse_inbound_payload()"]
    PARSE --> |"text content"| HANDLER["_handle_message()"]
    HANDLER --> |"is_allowed(client_id)?"| ACL{allowed?}
    ACL --> |No| WARN["log: Access denied"]
    ACL --> |Yes| IB["InboundMessage\n(channel=websocket,\nsender_id=client_id,\nchat_id=chat_id,\ncontent=text)"]
    IB --> PUB["bus.publish_inbound()"]
```

## handle_outbound

`send()` delivers a full `OutboundMessage` as a `message` event:

```python
async def send(self, msg: OutboundMessage) -> None:
    connection = self._connections.get(msg.chat_id)
    payload = {
        "event": "message",
        "text": msg.content,
    }
    if msg.media:
        payload["media"] = msg.media
    if msg.reply_to:
        payload["reply_to"] = msg.reply_to
    await self._safe_send(msg.chat_id, json.dumps(payload))
```

`send_delta()` delivers a streaming chunk:

```python
async def send_delta(self, chat_id, delta, metadata=None) -> None:
    if metadata.get("_stream_end"):
        body = {"event": "stream_end"}
    else:
        body = {"event": "delta", "text": delta}
    if metadata.get("_stream_id"):
        body["stream_id"] = metadata["_stream_id"]
    await self._safe_send(chat_id, json.dumps(body))
```

## Configuration (WebSocketConfig)

```python
class WebSocketConfig(Base):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/"
    token: str = ""                        # static secret token
    token_issue_path: str = ""             # HTTP GET endpoint for short-lived tokens
    token_issue_secret: str = ""           # secret for token issue endpoint
    token_ttl_s: int = 300                 # issued token TTL (30–86400s)
    websocket_requires_token: bool = True  # require token on WS handshake
    allow_from: list[str] = ["*"]          # allowed client_id values
    streaming: bool = True
    max_message_bytes: int = 1_048_576     # 1 MB max frame
    ping_interval_s: float = 20.0
    ping_timeout_s: float = 20.0
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
```

URL format: `ws://{host}:{port}{path}?client_id=...&token=...`

## Token Authentication

Two modes:

1. **Static token** — `token=...` query param must match the configured `token`
2. **Issued tokens** — client fetches a short-lived token via HTTP GET to `token_issue_path`, then presents it in the WS handshake

```mermaid
flowchart LR
    ISSUE["GET /token_issue_path\nAuthorization: Bearer <secret>"]
    GRANT["200: {token: nbwt_..., expires_in: 300}"]
    HANDSHAKE["WS handshake\n?token=nbwt_..."]
    VALID["Validate & consume token\n(single-use, TTL expires)"]

    ISSUE --> GRANT
    HANDSHAKE --> VALID
```

## SSL / WSS

Set both `ssl_certfile` and `ssl_keyfile` to enable WSS (TLS). If only one is set, startup raises an error.

```python
ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
ssl_context.load_cert_chain(certfile=cert, keyfile=key)
```
