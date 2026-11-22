import asyncio
import html as html_lib
import json
import logging
import os
import re
import socket
from datetime import timedelta, datetime
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

import aiohttp
from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import commands

BASE_DIR = Path(__file__).parent
LOGO_PATH = BASE_DIR / "hugolegacy_logo.png"
load_dotenv(BASE_DIR / ".env")
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "embed_color": "#00C8FC",
    "footer": "HugoLegacy",
    "ticket_title": "Open a ticket",
    "ticket_description": "Choose the category that best matches your request.",
    "ticket_welcome": "Please provide the reason you opened this ticket below. A staff member will be with you as soon as possible.",
    "ticket_log_channel_id": None,
    "ticket_admin_role_ids": [],
    "ticket_categories": [
        {"label": "General Support", "description": "Questions and general help", "emoji": "🎫"},
        {"label": "Report a User", "description": "Report a member or a problem", "emoji": "🚨"},
        {"label": "Partnership", "description": "Business and partnership requests", "emoji": "🤝"}
    ],
    "honeypot_channel_ids": [],
    "honeypot_title": "Honeypot Activated",
    "honeypot_description": "This channel is now a honeypot. Messages sent here by non-staff members will trigger an automatic softban.\n\nThis helps prevent hacked or compromised accounts from flooding the server. Keep this channel hidden from regular members and do not use it for normal conversation.\n\nStaff and bots are ignored. Use `/honeypot disable` to turn it off.",
    "locked_channels": {},
    "custom_embed_title": "HugoLegacy",
    "custom_embed_description": "Your message goes here.",
    "custom_embed_thumbnail": "",
    "custom_embed_image": "",
    "custom_embed_footer": "HugoLegacy",
    "scammer_title": "\U0001F6A8 Scam account — please report it!",
    "scammer_intro": "A scammer report was reviewed by staff.\n**Everyone: please report/block this account on {platform}** so it gets removed quickly. \U0001F64F",
    "scammer_reminder_title": "\u26A0\uFE0F Reminder",
    "scammer_reminder_text": "OpenX3 codes and unlocks are **never sold via DMs**. Use only `/vexaccess`, `/unlock` and the official OpenX3 website.",
    "scammer_platform": "TikTok",
    "scammer_account": "",
    "scammer_report": "",
    "scammer_submitted_by": "",
    "welcome_enabled": False,
    "welcome_channel_id": None,
    "welcome_title": "👋 Welcome!",
    "welcome_description": "Hey {user}, welcome to **{server}**!\n\nHave a look around and enjoy your stay!",
    "welcome_footer": "ID: {id}",
    "welcome_color": "",
    "welcome_show_info": True,
    "welcome_ping": False
}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hugolegacy")


def load_config():
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}
    merged = DEFAULT_CONFIG.copy()
    merged.update(data)
    return merged


def save_config(data):
    CONFIG_PATH.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")


config = load_config()
# Migrate the previous default branding while preserving any custom settings.
if config.get("footer") != "HugoLegacy":
    config["footer"] = "HugoLegacy"
config["embed_color"] = "#00C8FC"
config["welcome_color"] = ""
if config.get("embed_color") in ("#00C8FC", "#00C8FC"):
    config["embed_color"] = "#00C8FC"
save_config(config)


def embed_color():
    return discord.Color.from_str("#00C8FC")

def make_embed(title: Optional[str], description: str):
    e = discord.Embed(title=title or None, description=description, color=embed_color())
    e.set_footer(text="HugoLegacy")
    return e


def with_ticket_logo(embed: discord.Embed):
    """Attach the supplied HugoLegacy artwork as a ticket embed thumbnail."""
    if LOGO_PATH.exists():
        embed.set_thumbnail(url="attachment://hugolegacy_logo.png")
        return embed, discord.File(LOGO_PATH, filename="hugolegacy_logo.png")
    return embed, None


def is_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    role_ids = set(config.get("ticket_admin_role_ids", []))
    return any(role.id in role_ids for role in member.roles)


def admin_check():
    async def predicate(interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            raise app_commands.CheckFailure("You need Administrator permission or a configured ticket-admin role.")
        return True
    return app_commands.check(predicate)


def parse_duration(value: str) -> Optional[timedelta]:
    """Accept combinations such as 10m, 2h, 1d, 1w, or 1d2h30m."""
    matches = re.findall(r"(\d+)\s*(w|d|h|m|s)", value.lower())
    if not matches or re.sub(r"(\d+)\s*(w|d|h|m|s)", "", value.lower()).strip():
        return None
    seconds = 0
    for amount, unit in matches:
        seconds += int(amount) * {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    if seconds <= 0 or seconds > 28 * 86400:
        return None
    return timedelta(seconds=seconds)


def format_duration(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    parts = []
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        amount, total = divmod(total, size)
        if amount:
            parts.append(f"{amount}{unit}")
    return " ".join(parts) or "0s"


intents = discord.Intents.default()
intents.members = True
intents.message_content = True


class HugoLegacy(commands.Bot):
    async def setup_hook(self):
        self.add_view(TicketSelectView())
        self.add_view(TicketActionView())
        self.add_view(CloseRequestView())

        # Copy commands to one server, then clear old global copies.
        guild_id = int(os.getenv("GUILD_ID", "0"))
        if not guild_id:
            raise RuntimeError("GUILD_ID fehlt in deiner .env-Datei.")
        guild = discord.Object(id=guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        log.info("Slash commands synced to the configured server (global duplicates removed).")


bot = HugoLegacy(command_prefix="!", intents=intents)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Always acknowledge failed slash commands and log the underlying error."""
    original = getattr(error, "original", error)
    if isinstance(error, app_commands.MissingPermissions):
        message = "You need Administrator permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        missing = ", ".join(p.replace("_", " ").title() for p in error.missing_permissions)
        message = f"I am missing these permissions here: **{missing}**. Give my role these permissions (or Administrator) and try again."
    elif isinstance(error, app_commands.CheckFailure):
        message = "You are not allowed to use this command. Check your server permissions."
    else:
        log.error(
            "Unhandled application command error in /%s: %r",
            interaction.command.name if interaction.command else "unknown",
            original,
            exc_info=(type(original), original, original.__traceback__),
        )
        message = "Something went wrong while running this command. The error was logged in the bot console."
        if isinstance(interaction.user, discord.Member) and is_admin(interaction.user):
            message += f"\n\n`{type(original).__name__}: {str(original)[:300]}`"

    embed = make_embed("Command Error", message)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        log.exception("Could not send the slash-command error response.")


# ------------------------------ Moderation ------------------------------

@bot.tree.command(name="ban", description="Ban a member from this server.")
@app_commands.describe(member="The member to ban", reason="Why they are being banned")
@admin_check()
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member.id == interaction.user.id:
        return await interaction.response.send_message("You cannot ban yourself.", ephemeral=True)
    if member.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        return await interaction.response.send_message("You cannot ban someone with an equal or higher role.", ephemeral=True)
    try:
        await member.send(f"You were banned from **{interaction.guild.name}**. Reason: {reason}")
        could_dm = True
    except (discord.Forbidden, discord.HTTPException):
        could_dm = False
    try:
        await member.ban(reason=f"{reason} | By {interaction.user}")
    except discord.Forbidden:
        return await interaction.response.send_message("I don't have permission to ban that member.", ephemeral=True)
    e = make_embed("🔨 Member banned", f"**{member}** has been banned.\n**Reason:** {discord.utils.escape_markdown(reason)}")
    if not could_dm:
        e.add_field(name="DM status", value=f"👍 - couldn’t PM {member.mention}", inline=False)
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="unban", description="Unban a user by their Discord ID.")
@app_commands.describe(user_id="The banned user's ID (you can also paste a user mention)", reason="Reason for the unban")
@admin_check()
async def unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    # Accept a raw ID as well as <@123>, <@!123>, or a copied Discord mention.
    raw_id = user_id.strip()
    match = re.fullmatch(r"<@!?(\d+)>", raw_id)
    if match:
        raw_id = match.group(1)
    try:
        target_id = int(raw_id)
        if target_id <= 0 or target_id >= 2**64:
            raise ValueError
    except (TypeError, ValueError):
        return await interaction.response.send_message(
            embed=make_embed("Invalid User ID", "Enter the banned user's numeric Discord ID or paste their user mention."),
            ephemeral=True
        )

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            embed=make_embed("Unban Failed", "This command can only be used inside a server."),
            ephemeral=True
        )

    # Acknowledge immediately so the interaction cannot expire while Discord is queried.
    await interaction.response.defer(ephemeral=True)

    try:
        ban_entry = await guild.fetch_ban(discord.Object(id=target_id))
        banned_user = ban_entry.user
        await guild.unban(banned_user, reason=f"{reason} | By {interaction.user}")
    except discord.NotFound:
        return await interaction.followup.send(
            embed=make_embed("User Not Banned", f"No active ban was found for `{target_id}` in **{discord.utils.escape_markdown(guild.name)}**."),
            ephemeral=True
        )
    except discord.Forbidden:
        return await interaction.followup.send(
            embed=make_embed("Unban Failed", "Discord denied access. Make sure my **Ban Members** permission is enabled and my role is high enough."),
            ephemeral=True
        )
    except discord.HTTPException as exc:
        log.exception("Discord API error while unbanning user ID %s in guild %s", target_id, guild.id)
        return await interaction.followup.send(
            embed=make_embed("Unban Failed", f"Discord returned HTTP `{exc.status}` while checking or removing this ban. See the bot console for details."),
            ephemeral=True
        )
    except Exception:
        log.exception("Unexpected error in /unban for user ID %s", target_id)
        return await interaction.followup.send(
            embed=make_embed("Unban Failed", "An unexpected error occurred. The full details were written to the bot console."),
            ephemeral=True
        )

    await interaction.followup.send(
        embed=make_embed(
            "User Unbanned",
            f"**User:** {discord.utils.escape_markdown(str(banned_user))} (`{banned_user.id}`)\n"
            f"**Reason:** {discord.utils.escape_markdown(reason)}\n"
            f"**Moderator:** {interaction.user.mention}"
        ),
        ephemeral=True
    )


@bot.tree.command(name="timeout", description="Timeout a member for a duration.")
@app_commands.describe(member="The member to timeout", duration="Examples: 10m, 2h, 1d, 1w", reason="Why they are being timed out")
@admin_check()
async def timeout_member(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason provided"):
    delta = parse_duration(duration)
    if delta is None:
        return await interaction.response.send_message("Invalid duration. Use formats like `10m`, `2h`, `1d`, or `1w` (maximum 28 days).", ephemeral=True)
    if member.id == interaction.user.id:
        return await interaction.response.send_message("You cannot timeout yourself.", ephemeral=True)
    if member.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        return await interaction.response.send_message("You cannot timeout someone with an equal or higher role.", ephemeral=True)
    try:
        await member.timeout(delta, reason=f"{reason} | By {interaction.user}")
    except discord.Forbidden:
        return await interaction.response.send_message("I don't have permission to timeout that member.", ephemeral=True)
    e = make_embed("⏳ Timeout applied", f"👍 - {member.mention} has been timed out for **{format_duration(delta)}** by {interaction.user.mention}.\n**Reason:** {discord.utils.escape_markdown(reason)}")
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="softban", description="Ban and immediately unban a member to clear recent messages.")
@app_commands.describe(member="The member to softban", reason="Reason for the softban")
@admin_check()
async def softban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member.id == interaction.user.id:
        return await interaction.response.send_message("You cannot softban yourself.", ephemeral=True)
    if member.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        return await interaction.response.send_message("You cannot softban someone with an equal or higher role.", ephemeral=True)
    try:
        await member.ban(reason=f"Softban: {reason} | By {interaction.user}", delete_message_seconds=86400)
        await interaction.guild.unban(discord.Object(id=member.id), reason="Softban completed")
    except discord.Forbidden:
        return await interaction.response.send_message("I don't have permission to softban that member.", ephemeral=True)
    await interaction.response.send_message(f"Softbanned **{member}**.", ephemeral=True)


# ------------------------------ Channel moderation ------------------------------

def _permission_target(guild: discord.Guild, kind: str, target_id: int):
    if kind == "role":
        return guild.get_role(target_id)
    return guild.get_member(target_id) or discord.Object(id=target_id, type=discord.Member)


@bot.tree.command(name="purge", description="Delete a chosen number of recent messages (1-100).")
@app_commands.describe(count="How many messages should be deleted? (1-100)")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
async def purge(interaction: discord.Interaction, count: app_commands.Range[int, 1, 100]):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message(
            embed=make_embed("🧹 Purge Not Available", "This command only works in standard text channels."),
            ephemeral=True
        )
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await channel.purge(limit=count, bulk=True, reason=f"Purge by {interaction.user} ({interaction.user.id})")
    except discord.Forbidden:
        return await interaction.followup.send(
            embed=make_embed("🧹 Missing Permissions", "I am missing the **Manage Messages** permission."),
            ephemeral=True
        )
    except discord.HTTPException:
        return await interaction.followup.send(
            embed=make_embed("🧹 Purge Failed", "Discord could not delete the messages. Please try again."),
            ephemeral=True
        )
    e = make_embed("🧹 Messages Deleted", f"Deleted **{len(deleted)}** messages in {channel.mention}.\n**Moderator:** {interaction.user.mention}")
    await interaction.followup.send(embed=e, ephemeral=True)


LOCK_FIELDS = ("send_messages", "send_messages_in_threads")


def _overwrite_snapshot(overwrite: discord.PermissionOverwrite) -> dict:
    return {name: getattr(overwrite, name) for name in LOCK_FIELDS}


async def _restore_snapshot(channel: discord.TextChannel, snapshot: list, reason: str) -> list:
    """Restore saved overwrite values. Returns the IDs that could not be restored."""
    guild = channel.guild
    failed = []
    for item in snapshot:
        target = _permission_target(guild, item["kind"], item["id"])
        if target is None:
            continue
        # Old snapshots (v9 and earlier) only stored "send_messages".
        values = item.get("perms") or {"send_messages": item.get("send_messages")}
        current = channel.overwrites_for(target)
        current.update(**values)
        try:
            await channel.set_permissions(target, overwrite=None if current.is_empty() else current, reason=reason)
        except discord.HTTPException:
            log.exception("Could not restore channel permissions for %s in #%s", item["id"], channel.name)
            failed.append(str(item["id"]))
    return failed


@bot.tree.command(name="lock", description="Lock this channel for everyone without Administrator permission.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.bot_has_permissions(manage_roles=True, manage_channels=True)
async def lock(interaction: discord.Interaction):
    channel = interaction.channel
    guild = interaction.guild
    if isinstance(channel, discord.Thread):
        return await interaction.response.send_message(
            embed=make_embed("🔒 Cannot Lock Channel", "Please use `/lock` in the parent text channel, not inside a thread."),
            ephemeral=True
        )
    if guild is None or not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message(
            embed=make_embed("🔒 Cannot Lock Channel", "This channel type is not supported."),
            ephemeral=True
        )

    locked_channels = config.setdefault("locked_channels", {})
    key = str(channel.id)
    if key in locked_channels:
        return await interaction.response.send_message(
            embed=make_embed("🔒 Already Locked", "This channel is already locked by HugoLegacy."),
            ephemeral=True
        )

    await interaction.response.defer(ephemeral=False)

    me = guild.me
    deny = {name: False for name in LOCK_FIELDS}
    # (target, kind, new values). Order matters: the bot keeps its own access first.
    plan = []
    if not me.guild_permissions.administrator:
        my_perms = channel.permissions_for(me)
        keep = {name: True for name in LOCK_FIELDS if getattr(my_perms, name)}
        if keep:
            plan.append((me, "member", keep))

    # @everyone deny already beats the base permissions of every role. Only roles/members that
    # have an explicit "allow" in this channel need their own override changed as well.
    plan.append((guild.default_role, "role", deny))
    for target, overwrite in channel.overwrites.items():
        if target == guild.default_role or target.id == me.id:
            continue
        if isinstance(target, discord.Role):
            if target.permissions.administrator or target in me.roles:
                continue
            kind = "role"
        else:
            perms = getattr(target, "guild_permissions", None)
            if perms is not None and perms.administrator:
                continue
            kind = "member"
        allowed = {name: False for name in LOCK_FIELDS if getattr(overwrite, name) is True}
        if allowed:
            plan.append((target, kind, allowed))

    snapshot = []
    try:
        for target, kind, values in plan:
            overwrite = channel.overwrites_for(target)
            snapshot.append({"kind": kind, "id": target.id, "perms": _overwrite_snapshot(overwrite)})
            overwrite.update(**values)
            await channel.set_permissions(target, overwrite=overwrite, reason=f"Channel locked by {interaction.user}")
    except discord.HTTPException as exc:
        log.exception("Locking #%s failed", channel.name)
        await _restore_snapshot(channel, snapshot, "Rollback incomplete lock")
        return await interaction.followup.send(
            embed=make_embed(
                "🔒 Lock Failed",
                "I could not update the channel permissions (Discord error "
                f"`{exc.status}`). Make sure my role has **Manage Roles** and **Manage Channels** "
                "(or Administrator) and sits above the roles it has to change."
            ),
            ephemeral=True
        )

    locked_channels[key] = snapshot
    save_config(config)
    await interaction.followup.send(
        embed=make_embed("🔒 Channel Locked", f"{channel.mention} is now locked.\nOnly members with **Administrator** permission can continue sending messages.\n**Locked by:** {interaction.user.mention}")
    )


@bot.tree.command(name="unlock", description="Restore this channel's permissions from before /lock.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.checks.bot_has_permissions(manage_roles=True, manage_channels=True)
async def unlock(interaction: discord.Interaction):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message(
            embed=make_embed("🔓 Cannot Unlock Channel", "This command only works in standard text channels."),
            ephemeral=True
        )

    locked_channels = config.setdefault("locked_channels", {})
    snapshot = locked_channels.get(str(channel.id))
    if snapshot is None:
        return await interaction.response.send_message(
            embed=make_embed("🔓 Not Locked", "No previous `/lock` state is saved for this channel."),
            ephemeral=True
        )

    await interaction.response.defer(ephemeral=False)
    failed = await _restore_snapshot(channel, snapshot, f"Channel unlocked by {interaction.user}")

    if failed:
        return await interaction.followup.send(
            embed=make_embed("🔓 Partially Unlocked", "Some channel permissions could not be restored. Check the bot permissions and try again."),
            ephemeral=True
        )

    locked_channels.pop(str(channel.id), None)
    save_config(config)
    await interaction.followup.send(
        embed=make_embed("🔓 Channel Unlocked", f"The write permissions for {channel.mention} have been restored to their pre-lock state.\n**Unlocked by:** {interaction.user.mention}")
    )


# ------------------------------ Honeypot ------------------------------

honeypot_group = app_commands.Group(name="honeypot", description="Configure the anti-raid honeypot.")


@honeypot_group.command(name="setup", description="Turn this channel into a honeypot.")
@admin_check()
async def honeypot_setup(interaction: discord.Interaction):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message("Run this command in a text channel.", ephemeral=True)
    ids = set(config.get("honeypot_channel_ids", []))
    ids.add(channel.id)
    config["honeypot_channel_ids"] = list(ids)
    save_config(config)
    title = config.get("honeypot_title", "Honeypot Activated")
    description = config.get("honeypot_description", "This channel is now a honeypot.").replace("{channel}", channel.mention)
    e = make_embed(title, description)
    await interaction.response.send_message(embed=e)


@honeypot_group.command(name="text", description="Customize the honeypot setup embed title and description.")
@admin_check()
async def honeypot_text(interaction: discord.Interaction):
    modal = HoneypotTextModal()
    modal.honeypot_title.default = config.get("honeypot_title", "Honeypot Activated")
    modal.honeypot_description.default = config.get("honeypot_description", "")
    await interaction.response.send_modal(modal)


@honeypot_group.command(name="disable", description="Disable the honeypot in this channel.")
@admin_check()
async def honeypot_disable(interaction: discord.Interaction):
    ids = set(config.get("honeypot_channel_ids", []))
    ids.discard(interaction.channel_id)
    config["honeypot_channel_ids"] = list(ids)
    save_config(config)
    await interaction.response.send_message(embed=make_embed("Honeypot Disabled", "This channel is no longer monitored as a honeypot."))


bot.tree.add_command(honeypot_group)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    if message.channel.id in config.get("honeypot_channel_ids", []):
        member = message.author
        if isinstance(member, discord.Member) and not is_admin(member):
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            try:
                await message.guild.ban(member, reason="Honeypot triggered: possible compromised account", delete_message_seconds=86400)
                await message.guild.unban(discord.Object(id=member.id), reason="Honeypot softban")
                log.info("Honeypot softbanned %s in %s", member, message.guild.id)
            except discord.Forbidden:
                log.error("Missing permissions to softban honeypot trigger %s", member)
    await bot.process_commands(message)


# ------------------------------ TikTok post ------------------------------

TIKTOK_URL_RE = re.compile(r"https?://(?:[\w-]+\.)?tiktok\.com/[^\s<>]+", re.I)
TIKTOK_VIDEO_RE = re.compile(r"tiktok\.com/@([\w.\-]+)/(video|photo)/(\d+)", re.I)
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def extract_tiktok_url(text: str) -> Optional[str]:
    """Find a TikTok link anywhere in the text (full links, vm./vt. short links, /t/ links)."""
    match = TIKTOK_URL_RE.search(text or "")
    return match.group(0).rstrip(".,;)]\"'") if match else None


def clean_tiktok_url(url: str) -> str:
    """Strip tracking parameters. Full video links are rebuilt into their canonical form."""
    match = TIKTOK_VIDEO_RE.search(url)
    if match:
        user, kind, video_id = match.groups()
        return f"https://www.tiktok.com/@{user}/{kind.lower()}/{video_id}"
    return url.split("?", 1)[0].split("#", 1)[0]


async def resolve_tiktok_url(session: aiohttp.ClientSession, url: str) -> str:
    """Follow short links so oEmbed gets the real video URL."""
    url = clean_tiktok_url(url)
    if TIKTOK_VIDEO_RE.search(url):
        return url
    try:
        async with session.get(url, allow_redirects=True) as response:
            final = unquote(str(response.url))  # login redirects hide the video URL in a parameter
            return clean_tiktok_url(final)
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        log.warning("Could not resolve TikTok short link %s: %s", url, exc)
    return url


async def fetch_tiktok_oembed(session: aiohttp.ClientSession, url: str) -> dict:
    try:
        async with session.get("https://www.tiktok.com/oembed", params={"url": url}) as response:
            if response.status == 200:
                data = await response.json(content_type=None)
                if isinstance(data, dict):
                    return data
            else:
                log.warning("TikTok oEmbed returned HTTP %s for %s", response.status, url)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        log.warning("Could not retrieve TikTok oEmbed metadata: %s", exc)
    return {}


def _meta_content(page: str, prop: str) -> str:
    patterns = (
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]*content=["\']([^"\']*)["\']',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{re.escape(prop)}["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, page, re.I)
        if match:
            return html_lib.unescape(match.group(1)).strip()
    return ""


async def fetch_tiktok_page_meta(session: aiohttp.ClientSession, url: str) -> dict:
    """Fallback when oEmbed has nothing: read the Open Graph tags of the video page."""
    try:
        async with session.get(url, allow_redirects=True) as response:
            if response.status != 200:
                return {}
            raw = await response.content.read(400_000)
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        log.warning("Could not read TikTok page %s: %s", url, exc)
        return {}
    page = raw.decode("utf-8", errors="ignore")
    return {
        "title": _meta_content(page, "og:description") or _meta_content(page, "description"),
        "thumbnail_url": _meta_content(page, "og:image"),
    }


@bot.tree.command(name="post", description="Share a TikTok video in a gold HugoLegacy embed.")
@app_commands.describe(link="The TikTok link (normal link or short link like vm.tiktok.com)")
@admin_check()
async def post(interaction: discord.Interaction, link: str):
    url = extract_tiktok_url(link)
    if not url:
        return await interaction.response.send_message(
            embed=make_embed("Invalid TikTok Link", "Please provide a TikTok link, for example `https://www.tiktok.com/@name/video/123...` or `https://vm.tiktok.com/...`."),
            ephemeral=True
        )

    await interaction.response.defer()

    metadata = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), headers=BROWSER_HEADERS) as session:
        url = await resolve_tiktok_url(session, url)
        metadata = await fetch_tiktok_oembed(session, url)
        if not metadata.get("title") or not metadata.get("thumbnail_url"):
            page_meta = await fetch_tiktok_page_meta(session, url)
            for key, value in page_meta.items():
                if value and not metadata.get(key):
                    metadata[key] = value

    match = TIKTOK_VIDEO_RE.search(url)
    handle = (match.group(1) if match else (metadata.get("author_unique_id") or "")).strip().lstrip("@")
    display_name = (metadata.get("author_name") or "").strip()
    caption = (metadata.get("title") or "").strip()
    thumbnail_url = (metadata.get("thumbnail_url") or "").strip()

    embed = discord.Embed(color=embed_color())
    embed.set_author(name="HugoLegacy")

    if handle:
        embed.title = f"New TikTok from @{handle}"
        uploader = f"@{discord.utils.escape_markdown(handle)}"
        if display_name and display_name.lower().lstrip("@") != handle.lower():
            uploader = f"{discord.utils.escape_markdown(display_name)} ({uploader})"
        embed.add_field(name="👤 Uploaded by", value=uploader, inline=True)
    elif display_name:
        embed.title = f"New TikTok from {display_name.lstrip('@')}"
        embed.add_field(name="👤 Uploaded by", value=discord.utils.escape_markdown(display_name), inline=True)

    embed.add_field(name="✉️ Sent by", value=interaction.user.mention, inline=True)

    if caption:
        embed.description = caption[:4096]

    embed.add_field(name="🔗 Video link", value=f"[Open TikTok video]({url})", inline=False)

    if thumbnail_url and re.match(r"^https://", thumbnail_url, re.I):
        embed.set_thumbnail(url="attachment://hugolegacy_logo.png")

    embed.set_footer(text="HugoLegacy")
    await interaction.followup.send(embed=embed, file=discord.File(LOGO_PATH, filename="hugolegacy_logo.png"))


# ------------------------------ Tickets ------------------------------

def ticket_overwrites(guild: discord.Guild, opener: discord.Member):
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        opener: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
    }
    configured = set(config.get("ticket_admin_role_ids", []))
    for role in guild.roles:
        if role.permissions.administrator or role.id in configured:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
    return overwrites


# Users whose ticket is being created right now. Stops a second click/event from creating a duplicate
# while the first channel does not exist yet (asyncio is single-threaded, so check+add is atomic).
_tickets_in_progress: set = set()


async def create_ticket(interaction: discord.Interaction, category_label: str):
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Tickets can only be opened inside the server.", ephemeral=True)

    user_id = interaction.user.id
    if user_id in _tickets_in_progress:
        return await interaction.response.send_message("Your ticket is already being created. One moment please.", ephemeral=True)
    _tickets_in_progress.add(user_id)

    try:
        # Acknowledge right away: creating the channel can take longer than Discord's 3 second limit.
        await interaction.response.defer(ephemeral=True)

        existing = discord.utils.get(guild.text_channels, topic=f"donut-ticket:{user_id}")
        if existing:
            return await interaction.followup.send(f"You already have an open ticket: {existing.mention}", ephemeral=True)

        safe_name = re.sub(r"[^a-z0-9-]", "-", interaction.user.name.lower()).strip("-")[:18] or "member"
        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{safe_name}",
                topic=f"donut-ticket:{user_id}",
                overwrites=ticket_overwrites(guild, interaction.user),
                reason=f"Ticket opened by {interaction.user}"
            )
        except discord.Forbidden:
            return await interaction.followup.send("I am missing the **Manage Channels** permission, so I could not create your ticket.", ephemeral=True)

        welcome = make_embed(
            f"🎟️ Welcome to your ticket, {interaction.user.display_name}!",
            f"Hello {interaction.user.mention},\n\n{config.get('ticket_welcome', 'Please provide the reason you opened this ticket below.')}\n\n"
            f"**Category:** {category_label}\n\nPlease be patient, keep everything in this ticket, and do not share passwords or other sensitive information."
        )
        welcome, logo = with_ticket_logo(welcome)
        await channel.send(content=interaction.user.mention, embed=welcome, view=TicketActionView(), file=logo)
        await interaction.followup.send(f"Your ticket has been created: {channel.mention}", ephemeral=True)

        # Reset the dropdown on the panel so it does not stay on the last chosen category.
        if interaction.message is not None:
            try:
                await interaction.message.edit(view=TicketSelectView())
            except discord.HTTPException:
                pass
    finally:
        _tickets_in_progress.discard(user_id)


class TicketSelect(discord.ui.Select):
    def __init__(self):
        categories = config.get("ticket_categories", DEFAULT_CONFIG["ticket_categories"])[:25]
        options = []
        for item in categories:
            try:
                emoji_value = str(item.get("emoji") or "").strip()
                emoji = discord.PartialEmoji.from_str(emoji_value) if emoji_value.startswith(("<:", "<a:")) else (emoji_value or None)
                options.append(discord.SelectOption(
                    label=item.get("label", "Support")[:100],
                    description=item.get("description", "Open a ticket")[:100],
                    emoji=emoji
                ))
            except (TypeError, ValueError):
                options.append(discord.SelectOption(label=item.get("label", "Support")[:100], description=item.get("description", "Open a ticket")[:100]))
        if not options:
            options = [discord.SelectOption(label="Support", description="Open a support ticket")]
        super().__init__(placeholder="Choose a ticket category...", min_values=1, max_values=1, options=options, custom_id="donut:ticket-select")

    async def callback(self, interaction: discord.Interaction):
        await create_ticket(interaction, self.values[0])


class TicketSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())


class TicketActionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close ticket", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="donut:close-ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.channel, discord.TextChannel) or not interaction.channel.topic or not interaction.channel.topic.startswith("donut-ticket:"):
            return await interaction.response.send_message("This is not a HugoLegacy ticket.", ephemeral=True)
        opener_id = int(interaction.channel.topic.split(":", 1)[1])
        if interaction.user.id != opener_id and not (isinstance(interaction.user, discord.Member) and is_admin(interaction.user)):
            return await interaction.response.send_message("Only the ticket creator or staff can close this ticket.", ephemeral=True)
        await close_ticket_channel(interaction.channel, interaction.user)
        await interaction.response.send_message("Ticket closed and transcript processed.", ephemeral=True)


async def close_ticket_channel(channel: discord.TextChannel, closed_by: discord.abc.User):
    lines = []
    async for msg in channel.history(limit=500, oldest_first=True):
        stamp = msg.created_at.strftime("%Y-%m-%d %H:%M UTC")
        content = msg.clean_content.replace("\n", " ")
        if msg.attachments:
            content += " " + " ".join(a.url for a in msg.attachments)
        lines.append(f"[{stamp}] {msg.author}: {content}")
    transcript = "\n".join(lines) or "No messages."
    filename = f"{channel.name}-transcript.txt"
    file = discord.File(fp=__import__("io").BytesIO(transcript.encode("utf-8")), filename=filename)
    log_id = config.get("ticket_log_channel_id")
    log_channel = channel.guild.get_channel(log_id) if log_id else None
    if isinstance(log_channel, discord.TextChannel):
        e = make_embed("Ticket Closed", f"**Channel:** #{channel.name}\n**Closed by:** {closed_by.mention}")
        e, logo = with_ticket_logo(e)
        await log_channel.send(embed=e, files=[x for x in (file, logo) if x is not None])
    else:
        log.warning("Ticket log channel is not configured; transcript for %s was not delivered.", channel.id)
    await channel.delete(reason=f"Ticket closed by {closed_by}")


class CloseRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="donut:request-close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            return await interaction.response.send_message("Only administrators or configured ticket staff can decide this request.", ephemeral=True)
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("This button must be used inside a ticket.", ephemeral=True)
        if not interaction.channel.topic or not interaction.channel.topic.startswith("donut-ticket:"):
            return await interaction.response.send_message("This is not a HugoLegacy ticket.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await close_ticket_channel(interaction.channel, interaction.user)
        # The channel is deleted after its transcript is sent.
        try:
            await interaction.followup.send("Ticket closed and transcript sent to the log channel.", ephemeral=True)
        except discord.NotFound:
            pass

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary, emoji="↩️", custom_id="donut:request-decline")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            return await interaction.response.send_message("Only administrators or configured ticket staff can decline this request.", ephemeral=True)
        await interaction.response.send_modal(DeclineReasonModal())


class DeclineReasonModal(discord.ui.Modal, title="Decline Ticket Closure"):
    reason = discord.ui.TextInput(
        label="Why should this ticket stay open?",
        placeholder="Explain what still needs to be resolved...",
        style=discord.TextStyle.paragraph,
        min_length=5,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        e = make_embed("Ticket Will Remain Open", f"**Reason:**\n{self.reason.value}\n\n**Reviewed by:** {interaction.user.mention}")
        e, logo = with_ticket_logo(e)
        await interaction.response.send_message(embed=e, file=logo)
        try:
            notice = make_embed("Closure Declined", f"This ticket will remain open.\n\n**Reason:** {self.reason.value}\n**Reviewed by:** {interaction.user.mention}")
            notice, notice_logo = with_ticket_logo(notice)
            await interaction.channel.send(embed=notice, file=notice_logo)
        except discord.HTTPException:
            pass


class TicketPanelModal(discord.ui.Modal, title="Edit Ticket Panel"):
    panel_title = discord.ui.TextInput(label="Panel Title", max_length=256, required=True, default="Open a ticket")
    panel_description = discord.ui.TextInput(label="Panel Description", style=discord.TextStyle.paragraph, max_length=1500, required=True)
    welcome = discord.ui.TextInput(label="Ticket Welcome Message", style=discord.TextStyle.paragraph, max_length=1500, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        config["ticket_title"] = self.panel_title.value
        config["ticket_description"] = self.panel_description.value
        config["ticket_welcome"] = self.welcome.value
        save_config(config)
        await interaction.response.send_message(embed=make_embed("Saved", "The ticket title, description, and welcome message have been updated."), ephemeral=True)


class TicketCategoriesModal(discord.ui.Modal, title="Edit Ticket Categories"):
    categories_json = discord.ui.TextInput(
        label="Categories JSON (emoji: <:name:id>)",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
        default=json.dumps(DEFAULT_CONFIG["ticket_categories"], ensure_ascii=False, indent=2)
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            categories = json.loads(self.categories_json.value)
            if not isinstance(categories, list) or not 1 <= len(categories) <= 25:
                raise ValueError("Please provide between 1 and 25 categories.")
            for item in categories:
                if not all(k in item for k in ("label", "description", "emoji")):
                    raise ValueError("Each category must include label, description, and emoji.")
                if not str(item["label"]).strip() or len(str(item["label"])) > 100 or len(str(item["description"])) > 100:
                    raise ValueError("Category labels and descriptions must be 100 characters or fewer.")
                emoji_value = str(item["emoji"] or "").strip()
                if emoji_value.startswith(("<:", "<a:")):
                    parsed_emoji = discord.PartialEmoji.from_str(emoji_value)
                    if parsed_emoji.id is None:
                        raise ValueError(f"Invalid custom emoji format: {emoji_value}. Use <:name:id> or <a:name:id>.")
            config["ticket_categories"] = categories
            save_config(config)
            await interaction.response.send_message(embed=make_embed("Categories Saved", f"Saved {len(categories)} categories. Publish the panel again to update the dropdown."), ephemeral=True)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            await interaction.response.send_message(f"Invalid categories: {exc}", ephemeral=True)


class TicketAppearanceModal(discord.ui.Modal, title="Edit HugoLegacy Design"):
    color = discord.ui.TextInput(label="Embed color as HEX", max_length=7, required=True, default="#00C8FC")

    async def on_submit(self, interaction: discord.Interaction):
        value = self.color.value.strip()
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            return await interaction.response.send_message("Please enter a HEX color such as `#00C8FC`.", ephemeral=True)
        config["embed_color"] = value.upper()
        save_config(config)
        await interaction.response.send_message(embed=make_embed("HugoLegacy Design Saved", f"New embed color: `{value.upper()}`"), ephemeral=True)


class TicketLogModal(discord.ui.Modal, title="Set Ticket Log Channel"):
    channel_id = discord.ui.TextInput(label="Ticket log channel ID", max_length=25, required=True, placeholder="123456789012345678")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id = int(self.channel_id.value.strip())
            channel = interaction.guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ValueError
            config["ticket_log_channel_id"] = channel_id
            save_config(config)
            await interaction.response.send_message(f"Ticket logs will now be sent to {channel.mention}.", ephemeral=True)
        except (ValueError, AttributeError):
            await interaction.response.send_message("That channel ID does not belong to a text channel on this server.", ephemeral=True)


class TicketStaffModal(discord.ui.Modal, title="Edit Ticket Staff"):
    role_ids = discord.ui.TextInput(
        label="Staff role IDs (comma-separated)",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=False,
        placeholder="123456789012345678, 234567890123456789"
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            ids = [int(x.strip()) for x in self.role_ids.value.split(",") if x.strip()]
            missing = [rid for rid in ids if interaction.guild.get_role(rid) is None]
            if missing:
                return await interaction.response.send_message(f"These roles were not found: {missing}", ephemeral=True)
            config["ticket_admin_role_ids"] = ids
            save_config(config)
            await interaction.response.send_message("Ticket staff roles have been saved.", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("Please enter role IDs separated by commas.", ephemeral=True)


class HoneypotTextModal(discord.ui.Modal, title="Edit Honeypot Embed"):
    honeypot_title = discord.ui.TextInput(label="Embed Title", max_length=256, required=True)
    honeypot_description = discord.ui.TextInput(label="Embed Description", style=discord.TextStyle.paragraph, max_length=3500, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        config["honeypot_title"] = self.honeypot_title.value
        config["honeypot_description"] = self.honeypot_description.value
        save_config(config)
        await interaction.response.send_message(embed=make_embed("Honeypot Text Saved", "The honeypot setup embed has been updated."), ephemeral=True)


class TicketDashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Panel & Welcome", style=discord.ButtonStyle.primary, emoji="📝", row=0)
    async def edit_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = TicketPanelModal()
        modal.panel_title.default = config.get("ticket_title", "Open a ticket")
        modal.panel_description.default = config.get("ticket_description", "")
        modal.welcome.default = config.get("ticket_welcome", "")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Categories", style=discord.ButtonStyle.primary, emoji="📂", row=0)
    async def edit_categories(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = TicketCategoriesModal()
        modal.categories_json.default = json.dumps(config.get("ticket_categories", []), ensure_ascii=False, indent=2)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="HugoLegacy Color", style=discord.ButtonStyle.primary, emoji="🔹", row=0)
    async def edit_appearance(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = TicketAppearanceModal()
        modal.color.default = config.get("embed_color", "#00C8FC")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Log Channel", style=discord.ButtonStyle.secondary, emoji="📜", row=1)
    async def edit_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketLogModal())

    @discord.ui.button(label="Staff Roles", style=discord.ButtonStyle.secondary, emoji="🛡️", row=1)
    async def edit_staff(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = TicketStaffModal()
        modal.role_ids.default = ", ".join(str(x) for x in config.get("ticket_admin_role_ids", []))
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Publish Panel", style=discord.ButtonStyle.success, emoji="📨", row=1)
    async def publish_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Please use this in a text channel.", ephemeral=True)
        e = make_embed(config.get("ticket_title", "Open a ticket"), config.get("ticket_description", ""))
        e.add_field(name="How It Works", value="Choose a category below to create a private ticket. Only you and the server staff can see it.", inline=False)
        e, logo = with_ticket_logo(e)
        await interaction.channel.send(embed=e, view=TicketSelectView(), file=logo)
        await interaction.response.send_message("Ticket panel published here.", ephemeral=True)



class CustomEmbedModal(discord.ui.Modal, title="Edit Custom Embed"):
    title_input = discord.ui.TextInput(label="Embed title", max_length=256, required=False, placeholder="Announcement title")
    description_input = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, max_length=3500, required=False, placeholder="Write your embed text here")
    thumbnail_input = discord.ui.TextInput(label="Thumbnail (HugoLegacy logo is fixed)", max_length=500, required=False, placeholder="https://example.com/thumbnail.png")
    image_input = discord.ui.TextInput(label="Large image URL", max_length=500, required=False, placeholder="https://example.com/image.png")
    footer_input = discord.ui.TextInput(label="Footer text", max_length=2048, required=False, placeholder="HugoLegacy")

    async def on_submit(self, interaction: discord.Interaction):
        values = {
            "custom_embed_title": self.title_input.value.strip(),
            "custom_embed_description": self.description_input.value.strip(),
            "custom_embed_thumbnail": self.thumbnail_input.value.strip(),
            "custom_embed_image": self.image_input.value.strip(),
            "custom_embed_footer": self.footer_input.value.strip() or "HugoLegacy",
        }
        for key in ("custom_embed_thumbnail", "custom_embed_image"):
            value = values[key]
            if value and not re.match(r"^https?://", value, re.IGNORECASE):
                return await interaction.response.send_message(
                    f"`{key}` must be a direct HTTP(S) image URL.", ephemeral=True
                )
        config.update(values)
        save_config(config)
        await interaction.response.send_message(
            embed=make_embed("Embed Saved", "Your custom embed settings have been saved. Use **Preview** or **Publish** in the dashboard."),
            ephemeral=True
        )


class CustomEmbedDashboard(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Edit Content & Images", style=discord.ButtonStyle.primary, emoji="📝", row=0)
    async def edit_embed(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CustomEmbedModal()
        modal.title_input.default = config.get("custom_embed_title", "")
        modal.description_input.default = config.get("custom_embed_description", "")
        modal.thumbnail_input.default = config.get("custom_embed_thumbnail", "")
        modal.image_input.default = config.get("custom_embed_image", "")
        modal.footer_input.default = config.get("custom_embed_footer", "HugoLegacy")
        await interaction.response.send_modal(modal)

    def build_embed(self):
        title = config.get("custom_embed_title", "")
        description = config.get("custom_embed_description", "")
        e = make_embed(title or None, description or "\u200b")
        image = config.get("custom_embed_image", "").strip()
        e.set_thumbnail(url="attachment://hugolegacy_logo.png")
        if image:
            e.set_image(url=image)
        e.set_footer(text=config.get("custom_embed_footer", "HugoLegacy") or "HugoLegacy")
        return e

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary, emoji="👀", row=1)
    async def preview_embed(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=self.build_embed(), ephemeral=True, file=discord.File(LOGO_PATH, filename="hugolegacy_logo.png"))

    @discord.ui.button(label="Publish in this channel", style=discord.ButtonStyle.success, emoji="📨", row=1)
    async def publish_embed(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Use this dashboard in a text channel.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.channel.send(embed=self.build_embed(), file=discord.File(LOGO_PATH, filename="hugolegacy_logo.png"))
            await interaction.followup.send(embed=make_embed("Embed Published", f"Your embed was published in {interaction.channel.mention}."), ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(embed=make_embed("Missing Permissions", "I need **Send Messages** and **Embed Links** in this channel."), ephemeral=True)


@bot.tree.command(name="embed", description="Open the custom embed builder dashboard.")
@admin_check()
async def custom_embed_command(interaction: discord.Interaction):
    e = make_embed("HugoLegacy • Embed Dashboard", "Create your own blue-cyan embed. Edit its title, description, thumbnail, large image, and footer. Then preview it or publish it in this channel.")
    await interaction.response.send_message(embed=e, view=CustomEmbedDashboard(), ephemeral=True)


class ScammerReportModal(discord.ui.Modal, title="Edit Scam Report"):
    platform_input = discord.ui.TextInput(label="Platform", max_length=100, required=False, placeholder="TikTok")
    account_input = discord.ui.TextInput(label="Reported account", max_length=200, required=False, placeholder="@Scooterflash")
    report_input = discord.ui.TextInput(label="What was reported", style=discord.TextStyle.paragraph, max_length=500, required=False, placeholder="Licenzcode that never arrived")
    submitted_by_input = discord.ui.TextInput(label="Submitted by (name or ID)", max_length=200, required=False, placeholder="leon6778767")

    async def on_submit(self, interaction: discord.Interaction):
        config.update({
            "scammer_platform": self.platform_input.value.strip() or "TikTok",
            "scammer_account": self.account_input.value.strip(),
            "scammer_report": self.report_input.value.strip(),
            "scammer_submitted_by": self.submitted_by_input.value.strip(),
        })
        save_config(config)
        await interaction.response.send_message(
            embed=make_embed("Report Saved", "The scam report details have been saved. Use **Preview** or **Publish** in the dashboard."),
            ephemeral=True
        )


class ScammerBaseTextModal(discord.ui.Modal, title="Edit Scam Report Base Text"):
    title_input = discord.ui.TextInput(label="Embed title", max_length=256, required=False)
    intro_input = discord.ui.TextInput(
        label="Intro text (use {platform} as a placeholder)",
        style=discord.TextStyle.paragraph, max_length=1000, required=False
    )
    reminder_title_input = discord.ui.TextInput(label="Reminder field title", max_length=100, required=False)
    reminder_text_input = discord.ui.TextInput(label="Reminder field text", style=discord.TextStyle.paragraph, max_length=1000, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        config.update({
            "scammer_title": self.title_input.value.strip() or DEFAULT_CONFIG["scammer_title"],
            "scammer_intro": self.intro_input.value.strip() or DEFAULT_CONFIG["scammer_intro"],
            "scammer_reminder_title": self.reminder_title_input.value.strip() or DEFAULT_CONFIG["scammer_reminder_title"],
            "scammer_reminder_text": self.reminder_text_input.value.strip() or DEFAULT_CONFIG["scammer_reminder_text"],
        })
        save_config(config)
        await interaction.response.send_message(
            embed=make_embed("Base Text Saved", "The fixed parts of the `/scammer` embed (title, intro, reminder) have been updated."),
            ephemeral=True
        )


class ScammerDashboard(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    def build_embed(self, approved_by: str):
        platform = config.get("scammer_platform", "TikTok").strip() or "TikTok"
        account = config.get("scammer_account", "").strip() or "*not set*"
        report_text = config.get("scammer_report", "").strip() or "*not set*"
        submitted_by = config.get("scammer_submitted_by", "").strip() or "anonymous"

        intro_template = config.get("scammer_intro", DEFAULT_CONFIG["scammer_intro"])
        try:
            intro = intro_template.format(platform=platform)
        except (KeyError, IndexError, ValueError):
            intro = intro_template

        e = discord.Embed(
            title=config.get("scammer_title", DEFAULT_CONFIG["scammer_title"]),
            description=intro,
            color=discord.Color.red()
        )
        e.add_field(name="\U0001F3B5 Platform", value=platform, inline=True)
        e.add_field(name="\U0001F464 Account", value=account, inline=True)
        e.add_field(name="\U0001F4DD What was reported", value=report_text, inline=False)
        e.add_field(
            name=config.get("scammer_reminder_title", DEFAULT_CONFIG["scammer_reminder_title"]),
            value=config.get("scammer_reminder_text", DEFAULT_CONFIG["scammer_reminder_text"]),
            inline=False
        )
        e.set_thumbnail(url="attachment://hugolegacy_logo.png")
        timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
        e.set_footer(text=f"Approved by {approved_by} · submitted by {submitted_by} · {timestamp}")
        return e

    @discord.ui.button(label="Edit Report", style=discord.ButtonStyle.primary, emoji="📝", row=0)
    async def edit_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ScammerReportModal()
        modal.platform_input.default = config.get("scammer_platform", "TikTok")
        modal.account_input.default = config.get("scammer_account", "")
        modal.report_input.default = config.get("scammer_report", "")
        modal.submitted_by_input.default = config.get("scammer_submitted_by", "")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Edit Base Text", style=discord.ButtonStyle.secondary, emoji="🧱", row=0)
    async def edit_base_text(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ScammerBaseTextModal()
        modal.title_input.default = config.get("scammer_title", DEFAULT_CONFIG["scammer_title"])
        modal.intro_input.default = config.get("scammer_intro", DEFAULT_CONFIG["scammer_intro"])
        modal.reminder_title_input.default = config.get("scammer_reminder_title", DEFAULT_CONFIG["scammer_reminder_title"])
        modal.reminder_text_input.default = config.get("scammer_reminder_text", DEFAULT_CONFIG["scammer_reminder_text"])
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary, emoji="👀", row=1)
    async def preview_embed(self, interaction: discord.Interaction, button: discord.ui.Button):
        approved_by = interaction.user.display_name
        await interaction.response.send_message(
            embed=self.build_embed(approved_by), ephemeral=True,
            file=discord.File(LOGO_PATH, filename="hugolegacy_logo.png")
        )

    @discord.ui.button(label="Publish in this channel", style=discord.ButtonStyle.danger, emoji="📨", row=1)
    async def publish_embed(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("Use this dashboard in a text channel.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        approved_by = interaction.user.display_name
        try:
            await interaction.channel.send(
                content="@here",
                embed=self.build_embed(approved_by),
                file=discord.File(LOGO_PATH, filename="hugolegacy_logo.png"),
                allowed_mentions=discord.AllowedMentions(everyone=True)
            )
            await interaction.followup.send(embed=make_embed("Report Published", f"The scam report was published in {interaction.channel.mention}."), ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(embed=make_embed("Missing Permissions", "I need **Send Messages** and **Embed Links** in this channel."), ephemeral=True)


@bot.tree.command(name="scammer", description="Open the scam-report dashboard to edit and publish a scammer warning.")
@admin_check()
async def scammer_command(interaction: discord.Interaction):
    e = make_embed(
        "HugoLegacy • Scam Report Dashboard",
        "Fill in **Edit Report** (platform, account, what was reported, who submitted it) for this specific case. "
        "**Edit Base Text** changes the fixed parts (title, intro, reminder) that stay the same every time `/scammer` is used. "
        "Then use **Preview** or **Publish in this channel** — publishing pings @here."
    )
    await interaction.response.send_message(embed=e, view=ScammerDashboard(), ephemeral=True)


@bot.tree.command(name="ticketsetup", description="Open the HugoLegacy ticket dashboard.")
@admin_check()
async def ticketsetup(interaction: discord.Interaction):
    e = make_embed("HugoLegacy • Ticket Dashboard", "Manage ticket settings here: panel text, welcome message, categories, emojis, blue-cyan color, log channel, and staff roles.\n\nTo customize the honeypot embed, use `/honeypot text`. When finished, select **Publish Panel**.")
    e, logo = with_ticket_logo(e)
    await interaction.response.send_message(embed=e, view=TicketDashboardView(), ephemeral=True, file=logo)


@bot.tree.command(name="closerequest", description="Request a staff review to close this ticket.")
async def closerequest(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.TextChannel) or not interaction.channel.topic or not interaction.channel.topic.startswith("donut-ticket:"):
        return await interaction.response.send_message("Use this command inside a HugoLegacy ticket.", ephemeral=True)
    e = make_embed("Close Request", "Please review this ticket and choose whether it should be closed or remain open.")
    e.add_field(name="Requested By", value=interaction.user.mention, inline=False)
    e, logo = with_ticket_logo(e)
    await interaction.response.send_message(embed=e, view=CloseRequestView(), file=logo)


# ------------------------------ Welcome message ------------------------------

WELCOME_PLACEHOLDER_RE = re.compile(r"\{(user|username|name|server|count|id)\}")
WELCOME_HELP = (
    "`{user}` mention • `{username}` username • `{name}` display name\n"
    "`{server}` server name • `{count}` member number • `{id}` user ID"
)


def welcome_color() -> discord.Color:
    return embed_color()

def fill_welcome(text: str, member: discord.Member) -> str:
    values = {
        "user": member.mention,
        "username": member.name,
        "name": member.display_name,
        "server": member.guild.name,
        "count": str(member.guild.member_count or len(member.guild.members)),
        "id": str(member.id),
    }
    return WELCOME_PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], text or "")


def build_welcome_embed(member: discord.Member) -> discord.Embed:
    title = fill_welcome(config.get("welcome_title", ""), member).strip()
    description = fill_welcome(config.get("welcome_description", ""), member).strip()
    e = discord.Embed(title=title[:256] or None, description=description[:4096] or None, color=welcome_color())
    e.set_thumbnail(url="attachment://hugolegacy_logo.png")
    if config.get("welcome_show_info", True):
        count = member.guild.member_count or len(member.guild.members)
        e.add_field(name="Member", value=discord.utils.escape_markdown(member.name), inline=True)
        e.add_field(name="You are member", value=f"#{count}", inline=True)
        e.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
    footer = fill_welcome(config.get("welcome_footer", ""), member).strip()
    if footer:
        e.set_footer(text=footer[:2048])
    e.timestamp = discord.utils.utcnow()
    return e


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot or not config.get("welcome_enabled"):
        return
    channel = member.guild.get_channel(int(config.get("welcome_channel_id") or 0))
    if not isinstance(channel, discord.TextChannel):
        log.warning("Welcome is enabled, but the welcome channel was not found. Run /welcome setup.")
        return
    try:
        await channel.send(
            content=member.mention if config.get("welcome_ping") else None,
            embed=build_welcome_embed(member),
            file=discord.File(LOGO_PATH, filename="hugolegacy_logo.png"),
            allowed_mentions=discord.AllowedMentions(users=[member])
        )
    except discord.HTTPException:
        log.exception("Could not send the welcome message for %s", member)


class WelcomeMessageModal(discord.ui.Modal, title="Edit Welcome Message"):
    title_input = discord.ui.TextInput(label="Embed title", max_length=256, required=False)
    description_input = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph, max_length=3500, required=True)
    footer_input = discord.ui.TextInput(label="Footer (optional)", max_length=200, required=False)

    def __init__(self, dashboard: "WelcomeDashboard"):
        super().__init__()
        self.dashboard = dashboard
        self.title_input.default = config.get("welcome_title", "")
        self.description_input.default = config.get("welcome_description", "")
        self.footer_input.default = config.get("welcome_footer", "")

    async def on_submit(self, interaction: discord.Interaction):
        config["welcome_title"] = self.title_input.value.strip()
        config["welcome_description"] = self.description_input.value.strip()
        config["welcome_footer"] = self.footer_input.value.strip()
        save_config(config)
        await interaction.response.edit_message(embed=self.dashboard.status_embed(), view=self.dashboard)


class WelcomeColorModal(discord.ui.Modal, title="Welcome Embed Color"):
    color_input = discord.ui.TextInput(label="HEX color (empty = default)", max_length=7, required=False, placeholder="#00C8FC")

    def __init__(self, dashboard: "WelcomeDashboard"):
        super().__init__()
        self.dashboard = dashboard
        self.color_input.default = config.get("welcome_color", "")

    async def on_submit(self, interaction: discord.Interaction):
        value = self.color_input.value.strip()
        if value and not value.startswith("#"):
            value = "#" + value
        if value and not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            return await interaction.response.send_message("Please enter a HEX color such as `#00C8FC`.", ephemeral=True)
        config["welcome_color"] = value.upper()
        save_config(config)
        await interaction.response.edit_message(embed=self.dashboard.status_embed(), view=self.dashboard)


class WelcomeDashboard(discord.ui.View):
    def __init__(self, sample: discord.Member):
        super().__init__(timeout=600)
        self.sample = sample
        self._refresh_buttons()

    def _refresh_buttons(self):
        enabled = bool(config.get("welcome_enabled"))
        self.toggle_enabled.label = "Enabled" if enabled else "Disabled"
        self.toggle_enabled.style = discord.ButtonStyle.success if enabled else discord.ButtonStyle.danger
        self.toggle_info.label = "Info fields: on" if config.get("welcome_show_info", True) else "Info fields: off"
        self.toggle_ping.label = "Ping: on" if config.get("welcome_ping") else "Ping: off"

    def status_embed(self) -> discord.Embed:
        self._refresh_buttons()
        channel_id = config.get("welcome_channel_id")
        color = (config.get("welcome_color") or "").strip() or f"{config.get('embed_color', '#00C8FC')} (default)"
        lines = [
            f"**Status:** {'✅ Enabled' if config.get('welcome_enabled') else '⛔ Disabled'}",
            f"**Channel:** {f'<#{channel_id}>' if channel_id else 'not set – pick one below'}",
            f"**Color:** `{color}`",
            f"**Info fields (name, member number, account age):** {'on' if config.get('welcome_show_info', True) else 'off'}",
            f"**Ping the new member:** {'on' if config.get('welcome_ping') else 'off'}",
            "",
            "**Placeholders for title, message and footer:**",
            WELCOME_HELP,
        ]
        return make_embed("HugoLegacy • Welcome Setup", "\n".join(lines))

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        placeholder="Select the welcome channel...",
        min_values=1, max_values=1, row=0
    )
    async def pick_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        picked = select.values[0]
        channel = interaction.guild.get_channel(picked.id)
        if isinstance(channel, discord.TextChannel):
            perms = channel.permissions_for(interaction.guild.me)
            if not (perms.view_channel and perms.send_messages and perms.embed_links):
                return await interaction.response.send_message(
                    f"I need **View Channel**, **Send Messages** and **Embed Links** in {channel.mention}.", ephemeral=True
                )
        config["welcome_channel_id"] = picked.id
        save_config(config)
        await interaction.response.edit_message(embed=self.status_embed(), view=self)

    @discord.ui.button(label="Edit message", style=discord.ButtonStyle.primary, emoji="📝", row=1)
    async def edit_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WelcomeMessageModal(self))

    @discord.ui.button(label="Embed color", style=discord.ButtonStyle.primary, emoji="🎨", row=1)
    async def edit_color(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WelcomeColorModal(self))

    @discord.ui.button(label="Info fields", style=discord.ButtonStyle.secondary, emoji="🧾", row=2)
    async def toggle_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        config["welcome_show_info"] = not config.get("welcome_show_info", True)
        save_config(config)
        await interaction.response.edit_message(embed=self.status_embed(), view=self)

    @discord.ui.button(label="Ping", style=discord.ButtonStyle.secondary, emoji="🔔", row=2)
    async def toggle_ping(self, interaction: discord.Interaction, button: discord.ui.Button):
        config["welcome_ping"] = not config.get("welcome_ping", False)
        save_config(config)
        await interaction.response.edit_message(embed=self.status_embed(), view=self)

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary, emoji="👀", row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_welcome_embed(self.sample), ephemeral=True)

    @discord.ui.button(label="Disabled", style=discord.ButtonStyle.danger, row=3)
    async def toggle_enabled(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not config.get("welcome_enabled") and not config.get("welcome_channel_id"):
            return await interaction.response.send_message("Pick a welcome channel first.", ephemeral=True)
        config["welcome_enabled"] = not config.get("welcome_enabled", False)
        save_config(config)
        await interaction.response.edit_message(embed=self.status_embed(), view=self)


welcome_group = app_commands.Group(name="welcome", description="Configure the welcome message for new members.")


@welcome_group.command(name="setup", description="Set the welcome channel, message and embed color.")
@admin_check()
async def welcome_setup(interaction: discord.Interaction):
    view = WelcomeDashboard(interaction.user)
    await interaction.response.send_message(embed=view.status_embed(), view=view, ephemeral=True)


@welcome_group.command(name="test", description="Send a test welcome message for yourself to the welcome channel.")
@admin_check()
async def welcome_test(interaction: discord.Interaction):
    channel = interaction.guild.get_channel(int(config.get("welcome_channel_id") or 0))
    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message("No welcome channel is set. Use `/welcome setup` first.", ephemeral=True)
    try:
        await channel.send(embed=build_welcome_embed(interaction.user))
    except discord.Forbidden:
        return await interaction.response.send_message(f"I cannot send messages in {channel.mention}.", ephemeral=True)
    await interaction.response.send_message(f"Test welcome message sent to {channel.mention}.", ephemeral=True)


bot.tree.add_command(welcome_group)


# ------------------------------ Startup ------------------------------

@bot.event
async def on_ready():
    log.info("HugoLegacy is online as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")


def acquire_single_instance_lock():
    """Refuse to start if another copy of this bot already runs on this PC."""
    port = int(os.getenv("DONUT_LOCK_PORT", "48611"))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit(
            "HugoLegacy is already running in another window/process. "
            "Two copies answer every click twice (for example opening two tickets). Close the old one first."
        )
    return sock


def main():
    instance_lock = acquire_single_instance_lock()  # keep a reference so the port stays reserved
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Set the DISCORD_TOKEN environment variable before starting the bot.")
    bot.run(token)


if __name__ == "__main__":
    main()
