"""Microsoft Teams channel implementation using Bot Framework SDK."""

from __future__ import annotations

import asyncio
import importlib.util
import re
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger
from pydantic import Field

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import Base
from nanobot.utils.helpers import split_message

BOTBUILDER_AVAILABLE = (
    importlib.util.find_spec("botbuilder") is not None
    and importlib.util.find_spec("aiohttp") is not None
)

if TYPE_CHECKING:
    from botbuilder.core import BotFrameworkAdapter, TurnContext
    from botbuilder.schema import Activity, ConversationReference

if BOTBUILDER_AVAILABLE:
    from aiohttp import web
    from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, TurnContext
    from botbuilder.schema import Activity, ActivityTypes, ConversationReference

MAX_MESSAGE_LEN = 28_000  # Teams practical text limit per message


class TeamsDMConfig(Base):
    """DM / personal-chat access policy for Teams."""

    enabled: bool = True
    policy: Literal["open", "allowlist"] = "open"
    allow_from: list[str] = Field(default_factory=list)


class TeamsConfig(Base):
    """Microsoft Teams channel configuration."""

    enabled: bool = False
    app_id: str = ""
    app_password: str = ""
    host: str = "0.0.0.0"
    port: int = 3978
    webhook_path: str = "/api/messages"

    # Access control ─────────────────────────────────────────────────────────
    # allowFrom: AAD user object-IDs (GUIDs) or UPNs (email-style).
    # Empty list → deny all; ["*"] → allow everyone.
    allow_from: list[str] = Field(default_factory=list)
    # allow_tenants: restrict to specific Azure AD tenant IDs.
    # Empty list → accept any tenant.
    allow_tenants: list[str] = Field(default_factory=list)
    # group_policy: how the bot behaves inside Teams channels / group chats.
    #   "mention"   – only respond when @mentioned (default, recommended)
    #   "open"      – respond to every message
    #   "allowlist" – only respond in conversations listed in group_allow_from
    group_policy: Literal["mention", "open", "allowlist"] = "mention"
    # group_allow_from: conversation IDs allowed when group_policy="allowlist".
    group_allow_from: list[str] = Field(default_factory=list)
    # dm: fine-grained control over 1-to-1 (personal) conversations.
    dm: TeamsDMConfig = Field(default_factory=TeamsDMConfig)

    streaming: bool = False


class TeamsChannel(BaseChannel):
    """
    Microsoft Teams bot channel.

    Uses the Bot Framework SDK (botbuilder-core) + aiohttp to expose an
    incoming-webhook endpoint.  Teams delivers every message as an HTTP POST
    to ``webhook_path``; the SDK validates the JWT Bearer token and hands us
    a ``TurnContext``.  Outbound messages are sent as *proactive* replies via
    the stored ``ConversationReference``.

    Access-control layers (applied in order):
    1. Tenant allowlist  (``allow_tenants``) — optional organisation-level gate
    2. Conversation-type policy:
       - DMs  → ``dm.enabled`` + ``dm.policy`` / ``dm.allow_from``
       - Group → ``group_policy`` / ``group_allow_from``
    3. Global user allowlist (``allow_from``) via the inherited ``is_allowed()``
    """

    name = "teams"
    display_name = "Microsoft Teams"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return TeamsConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus) -> None:
        if isinstance(config, dict):
            config = TeamsConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: TeamsConfig = config
        self._adapter: BotFrameworkAdapter | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._bot_id: str = self.config.app_id
        # chat_id → ConversationReference for proactive/reply sends
        self._conv_refs: dict[str, ConversationReference] = {}

    # ──────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the aiohttp webhook server and begin listening for Teams messages."""
        if not BOTBUILDER_AVAILABLE:
            logger.error(
                "botbuilder-core / aiohttp not installed. "
                "Run: pip install nanobot-ai[teams]"
            )
            return
        if not self.config.app_id or not self.config.app_password:
            logger.error("Teams: app_id and app_password must both be configured")
            return

        settings = BotFrameworkAdapterSettings(
            app_id=self.config.app_id,
            app_password=self.config.app_password,
        )
        self._adapter = BotFrameworkAdapter(settings)
        self._adapter.on_turn_error = self._on_error  # type: ignore[assignment]
        self._bot_id = self.config.app_id

        app = web.Application()
        app.router.add_post(self.config.webhook_path, self._handle_webhook)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.config.host, self.config.port)
        await self._site.start()

        self._running = True
        logger.info(
            "Teams bot listening on {}:{}{} (appId={})",
            self.config.host,
            self.config.port,
            self.config.webhook_path,
            self.config.app_id,
        )

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the webhook server and release resources."""
        self._running = False
        if self._site:
            try:
                await self._site.stop()
            except Exception as e:
                logger.warning("Teams: site stop error: {}", e)
            self._site = None
        if self._runner:
            try:
                await self._runner.cleanup()
            except Exception as e:
                logger.warning("Teams: runner cleanup error: {}", e)
            self._runner = None
        self._adapter = None

    # ──────────────────────────────────────────────────────────────────────────
    # Outbound
    # ──────────────────────────────────────────────────────────────────────────

    async def send(self, msg: OutboundMessage) -> None:
        """Send a reply to a Teams conversation (proactive / continue_conversation)."""
        if not self._adapter:
            logger.warning("Teams: adapter not initialised; dropping message")
            return

        ref = self._conv_refs.get(msg.chat_id)
        if not ref:
            logger.warning("Teams: no conversation reference for chat_id={}", msg.chat_id)
            return

        text = msg.content or ""
        chunks = split_message(text, MAX_MESSAGE_LEN) if text else []

        media_notes = [f"[file: {p}]" for p in (msg.media or [])]

        async def _send_turn(turn_context: TurnContext) -> None:
            for chunk in chunks:
                await turn_context.send_activity(
                    Activity(type=ActivityTypes.message, text=chunk)
                )
            for note in media_notes:
                await turn_context.send_activity(
                    Activity(type=ActivityTypes.message, text=note)
                )
            if not chunks and not media_notes:
                # Send empty placeholder so the conversation isn't left hanging
                await turn_context.send_activity(
                    Activity(type=ActivityTypes.message, text=" ")
                )

        try:
            await self._adapter.continue_conversation(ref, _send_turn, self._bot_id)
        except Exception as e:
            logger.error("Teams: send failed for chat_id={}: {}", msg.chat_id, e)
            raise

    # ──────────────────────────────────────────────────────────────────────────
    # Inbound webhook
    # ──────────────────────────────────────────────────────────────────────────

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """Receive and authenticate an incoming Bot Framework Activity."""
        if not self._adapter:
            return web.Response(status=500, text="Adapter not initialized")

        try:
            body = await request.text()
            auth_header = request.headers.get("Authorization", "")
            response = await self._adapter.process_activity(body, auth_header, self._on_turn)
            if response:
                return web.Response(status=response.status, body=response.body)
            return web.Response(status=200)
        except Exception as e:
            logger.error("Teams: webhook error: {}", e)
            return web.Response(status=500, text=str(e))

    async def _on_turn(self, turn_context: TurnContext) -> None:
        """Process a single Bot Framework turn (one incoming activity)."""
        activity: Activity = turn_context.activity

        if activity.type != ActivityTypes.message:
            return

        sender = activity.from_property
        conv = activity.conversation
        if not sender or not conv:
            return

        sender_id: str = sender.id or ""
        chat_id: str = conv.id or ""

        if not sender_id or not chat_id:
            return

        # ── Tenant-level gate ────────────────────────────────────────────────
        tenant_id = self._extract_tenant_id(activity)
        if self.config.allow_tenants and tenant_id not in self.config.allow_tenants:
            logger.warning(
                "Teams: denied — tenant {} not in allow_tenants (sender={})",
                tenant_id,
                sender_id,
            )
            return

        # ── Conversation-type detection ─────────────────────────────────────
        conv_type = conv.conversation_type or "personal"
        is_group = conv_type in ("channel", "groupChat")

        # Store conversation reference so we can send replies later
        ref = TurnContext.get_conversation_reference(activity)
        self._conv_refs[chat_id] = ref

        # ── Channel/group policy ─────────────────────────────────────────────
        if not self._check_policy(sender_id, chat_id, is_group):
            logger.warning(
                "Teams: policy denied sender={} chat={} is_group={}",
                sender_id,
                chat_id,
                is_group,
            )
            return

        text = activity.text or ""
        if not text.strip():
            return

        if is_group:
            if not self._should_respond_in_group(activity, text):
                return
            text = self._strip_bot_mention(text)

        if not text.strip():
            return

        session_key = f"teams:{chat_id}" if is_group else None

        try:
            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=text.strip(),
                metadata={
                    "teams": {
                        "sender_name": sender.name or "",
                        "conv_type": conv_type,
                        "tenant_id": tenant_id,
                        "activity_id": activity.id or "",
                    }
                },
                session_key=session_key,
            )
        except Exception:
            logger.exception("Teams: unhandled error from sender={}", sender_id)

    # ──────────────────────────────────────────────────────────────────────────
    # Access control helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _check_policy(self, sender_id: str, chat_id: str, is_group: bool) -> bool:
        """
        Conversation-type policy check (layer 2 of 3).

        Layer 1 (tenant) is checked before this method.
        Layer 3 (global allow_from) is enforced inside BaseChannel._handle_message
        via the inherited is_allowed() call.
        """
        if not is_group:
            if not self.config.dm.enabled:
                return False
            if self.config.dm.policy == "allowlist":
                dm_list = self.config.dm.allow_from or self.config.allow_from
                return "*" in dm_list or sender_id in dm_list
            # "open" DM policy — gate on the top-level allow_from is applied
            # in layer 3; nothing extra to check here.
            return True

        # Group / channel conversation
        if self.config.group_policy == "allowlist":
            return chat_id in self.config.group_allow_from
        # "mention" and "open" reach _should_respond_in_group next
        return True

    def _should_respond_in_group(self, activity: Activity, text: str) -> bool:
        """Check whether to respond based on group_policy."""
        if self.config.group_policy == "open":
            return True

        if self.config.group_policy in ("mention", "allowlist"):
            # For "allowlist" the conversation-ID gate already fired in
            # _check_policy; here we additionally require a @mention.
            bot_id = self._bot_id
            for entity in activity.entities or []:
                if entity.type == "mention":
                    mentioned = entity.additional_properties.get("mentioned") or {}
                    if isinstance(mentioned, dict) and mentioned.get("id") == bot_id:
                        return True
                    if getattr(mentioned, "id", None) == bot_id:
                        return True
            # Fallback: raw <at> tag in HTML-encoded text
            return bool(re.search(r"<at\b", text or "", re.IGNORECASE))

        return False

    @staticmethod
    def _strip_bot_mention(text: str) -> str:
        """Remove Teams ``<at>Bot Name</at>`` mention tags from text."""
        return re.sub(r"<at>[^<]*</at>\s*", "", text or "").strip()

    @staticmethod
    def _extract_tenant_id(activity: Activity) -> str:
        """Pull the tenant ID out of channel_data (Teams-specific field)."""
        channel_data = activity.channel_data
        if isinstance(channel_data, dict):
            tenant = channel_data.get("tenant") or {}
            if isinstance(tenant, dict):
                return tenant.get("id", "")
        return ""

    # ──────────────────────────────────────────────────────────────────────────
    # Error handler
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def _on_error(context: TurnContext, error: Exception) -> None:
        """Log unhandled Bot Framework errors without crashing the server."""
        logger.error("Teams: unhandled adapter error: {}", error)
        try:
            await context.send_activity("Sorry, something went wrong.")
        except Exception:
            pass
