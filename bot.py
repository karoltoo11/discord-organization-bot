from __future__ import annotations

import asyncio
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final, Literal

import discord
import pytz
from discord import app_commands
from discord.ext import commands, tasks


SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=ENV_PATH)
except ImportError:
    print("Warning: Library 'python-dotenv' is not installed. Run: pip install python-dotenv")


GUILD_IDS_RAW = os.getenv("DISCORD_GUILD_ID", "").strip()
GUILD_IDS: list[int] = [
    int(gid.strip()) for gid in GUILD_IDS_RAW.split(",") if gid.strip().isdigit()
]

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()


WARSAW: Final = pytz.timezone("Europe/Warsaw")
DATE_FORMATS: Final = ("%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M")
NO_LIMIT_WORDS: Final = {"none", "no limit", "unlimited", "brak", "bez limitu", "bez_limitu", "∞"}
EMBED_COLOR: Final = discord.Colour.gold()
LFG_COLOR: Final = discord.Colour.green()
TODO_COLOR: Final = discord.Colour.blue()
REMINDER_COLOR: Final = discord.Colour.purple()
TRAP_COLOR: Final = discord.Colour.dark_red()



def parse_end_date(value: str) -> datetime:
    for date_format in DATE_FORMATS:
        try:
            dt = datetime.strptime(value.strip(), date_format)
            return WARSAW.localize(dt)
        except ValueError:
            continue
    raise ValueError("Please provide a date in `DD.MM.YYYY HH:MM` format, e.g. `31.12.2026 20:00`.")


def parse_reminder_time(value: str) -> datetime:
    cleaned = value.strip()
    now = datetime.now(WARSAW)

    rel_matches = re.findall(r"(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)?", cleaned.lower())
    if rel_matches and all(m[0] for m in rel_matches) and not any(c in cleaned for c in (":", ".", "-")):
        total_seconds = 0
        for amount_str, unit in rel_matches:
            amount = int(amount_str)
            unit = unit.lower() if unit else "m"
            if unit in ("s", "sec", "secs", "second", "seconds"):
                total_seconds += amount
            elif unit in ("m", "min", "mins", "minute", "minutes"):
                total_seconds += amount * 60
            elif unit in ("h", "hr", "hrs", "hour", "hours"):
                total_seconds += amount * 3600
            elif unit in ("d", "day", "days"):
                total_seconds += amount * 86400
        if total_seconds > 0:
            return now + timedelta(seconds=total_seconds)


    try:
        t = datetime.strptime(cleaned, "%H:%M").time()
        candidate = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    except ValueError:
        pass


    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return WARSAW.localize(dt)
        except ValueError:
            pass

    raise ValueError(
        "Invalid time format. Examples:\n"
        "• Relative: `15m`, `2h`, `1d`, `30m`\n"
        "• Time: `20:30` (today or tomorrow)\n"
        "• Full date: `31.12.2026 20:00`"
    )


def parse_limit(value: str) -> int | None:
    cleaned = value.strip().lower()
    if cleaned in NO_LIMIT_WORDS:
        return None

    try:
        limit = int(cleaned)
    except ValueError as error:
        raise ValueError("Limit must be a positive number or the word `none`.") from error

    if limit < 1:
        raise ValueError("Limit must be greater than zero.")
    return limit


def participants_text(count: int, max_people: int | None) -> str:
    return f"**{count} / {'no limit' if max_people is None else max_people}**"


class ContestView(discord.ui.View):
    def __init__(self, cog: ContestCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Join / Leave",
        emoji="🎉",
        style=discord.ButtonStyle.primary,
        custom_id="contest:toggle",
    )
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.message is None:
            await interaction.response.send_message(
                "Could not read the contest message.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        result, count, max_people, winners = await self.cog.toggle_participant(
            interaction.message.id, interaction.user.id
        )

        messages = {
            "joined": "You entered the contest.",
            "left": "You left the contest.",
            "full": "All spots in this contest are taken.",
            "ended": "This contest has already ended.",
            "missing": "This contest is no longer available.",
        }

        if result in {"joined", "left"}:
            await self.cog.refresh_message(interaction.message, count, max_people)
        elif result == "ended":
            await self.cog.mark_message_closed(interaction.message, winners)

        await interaction.followup.send(messages[result], ephemeral=True)


class ClosedContestView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Contest Ended",
                style=discord.ButtonStyle.secondary,
                custom_id="contest:closed",
                disabled=True,
            )
        )



class EventView(discord.ui.View):
    def __init__(self, cog: ContestCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Count me in!",
        emoji="🎉",
        style=discord.ButtonStyle.primary,
        custom_id="event:toggle",
    )
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.message is None:
            await interaction.response.send_message(
                "Could not read the event message.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        result, count, max_people = await self.cog.toggle_event_participant(
            interaction.message.id, interaction.user.id
        )

        messages = {
            "joined": "You signed up for the event.",
            "left": "You left the event.",
            "full": "All spots for this event are already taken.",
            "started": "This event has already started or ended.",
            "missing": "This event is no longer available.",
        }

        if result in {"joined", "left"}:
            await self.cog.refresh_event_message(interaction.message, count, max_people)

        await interaction.followup.send(messages[result], ephemeral=True)


class ClosedEventView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Event Started",
                style=discord.ButtonStyle.secondary,
                custom_id="event:closed",
                disabled=True,
            )
        )



class LfgView(discord.ui.View):
    def __init__(self, cog: ContestCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Join / Leave",
        emoji="🎮",
        style=discord.ButtonStyle.success,
        custom_id="lfg:toggle",
    )
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.message is None:
            await interaction.response.send_message(
                "Could not read the LFG post.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        result, count, max_people = await self.cog.toggle_lfg_participant(
            interaction.message.id, interaction.user.id
        )

        messages = {
            "joined": "You joined the party!",
            "left": "You left the party.",
            "full": "The party is already full!",
            "started": "This match has already started or been closed.",
            "missing": "This LFG post is no longer available.",
        }

        if result in {"joined", "left"}:
            await self.cog.refresh_lfg_message(interaction.message, count, max_people)

        await interaction.followup.send(messages[result], ephemeral=True)


class ClosedLfgView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Party Closed / In Progress",
                style=discord.ButtonStyle.secondary,
                custom_id="lfg:closed",
                disabled=True,
            )
        )



class ContestCog(commands.Cog):
    def __init__(self, bot: commands.Bot, database_path: str = "contests.sqlite3") -> None:
        self.bot = bot
        db_file = SCRIPT_DIR / database_path
        self.db = sqlite3.connect(db_file, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db_lock = asyncio.Lock()
        self.trap_channels: set[int] = set()
        self._create_tables()

    def _create_tables(self) -> None:
        self.db.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS contests (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                prize TEXT NOT NULL,
                ends_at INTEGER NOT NULL,
                max_people INTEGER,
                closed INTEGER NOT NULL DEFAULT 0,
                winner_count INTEGER NOT NULL DEFAULT 1,
                drawn INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS participants (
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at INTEGER NOT NULL,
                PRIMARY KEY (message_id, user_id),
                FOREIGN KEY (message_id) REFERENCES contests(message_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS winners (
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                drawn_at INTEGER NOT NULL,
                PRIMARY KEY (message_id, user_id),
                FOREIGN KEY (message_id) REFERENCES contests(message_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS events (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                starts_at INTEGER NOT NULL,
                max_people INTEGER,
                started INTEGER NOT NULL DEFAULT 0,
                thread_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS event_participants (
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at INTEGER NOT NULL,
                PRIMARY KEY (message_id, user_id),
                FOREIGN KEY (message_id) REFERENCES events(message_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS lfg (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                host_id INTEGER NOT NULL,
                game_name TEXT NOT NULL,
                starts_at INTEGER NOT NULL,
                max_people INTEGER,
                closed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS lfg_participants (
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at INTEGER NOT NULL,
                PRIMARY KEY (message_id, user_id),
                FOREIGN KEY (message_id) REFERENCES lfg(message_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                creator_id INTEGER NOT NULL,
                assigned_id INTEGER,
                content TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'Medium',
                status TEXT NOT NULL DEFAULT 'todo',
                created_at INTEGER NOT NULL,
                completed_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER,
                guild_id INTEGER,
                remind_at INTEGER NOT NULL,
                content TEXT NOT NULL,
                send_dm INTEGER NOT NULL DEFAULT 1,
                delivered INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS trap_channels (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                set_by INTEGER NOT NULL
            );
            """
        )
        columns = {
            row["name"] for row in self.db.execute("PRAGMA table_info(contests)").fetchall()
        }
        if "winner_count" not in columns:
            self.db.execute("ALTER TABLE contests ADD COLUMN winner_count INTEGER NOT NULL DEFAULT 1")
        if "drawn" not in columns:
            self.db.execute("ALTER TABLE contests ADD COLUMN drawn INTEGER NOT NULL DEFAULT 0")
        self.db.commit()

        # Cache active trap channels
        self.trap_channels = {
            row["channel_id"] for row in self.db.execute("SELECT channel_id FROM trap_channels").fetchall()
        }

    def is_contest_or_event_message(self, message_id: int) -> bool:
        c = self.db.execute("SELECT 1 FROM contests WHERE message_id = ?", (message_id,)).fetchone()
        if c:
            return True
        e = self.db.execute("SELECT 1 FROM events WHERE message_id = ?", (message_id,)).fetchone()
        if e:
            return True
        l = self.db.execute("SELECT 1 FROM lfg WHERE message_id = ?", (message_id,)).fetchone()
        return l is not None

    async def cog_load(self) -> None:
        self.bot.add_view(ContestView(self))
        self.bot.add_view(EventView(self))
        self.bot.add_view(LfgView(self))
        self.background_checker.start()

    async def cog_unload(self) -> None:
        self.background_checker.cancel()
        self.db.close()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Handles trap channels: automatically kicks any non-admin who sends a message."""
        if not message.guild or message.author.bot:
            return

        if message.channel.id in self.trap_channels:
            # Delete the intruder message
            try:
                await message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass

            if isinstance(message.author, discord.Member):
                # Protect server owner and administrators
                if message.author.id == message.guild.owner_id or message.author.guild_permissions.administrator:
                    try:
                        await message.channel.send(
                            f"⚠️ {message.author.mention}, this is a trap channel! You were not kicked because you are an administrator.",
                            delete_after=6,
                        )
                    except discord.HTTPException:
                        pass
                    return

                # Send DM notification before kick
                try:
                    await message.author.send(
                        f"🪤 You were kicked from **{message.guild.name}** for sending a message in a designated trap channel (<#{message.channel.id}>)!"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

                # Kick member
                try:
                    await message.author.kick(reason=f"Sent a message in trap channel (#{message.channel.name})")
                    try:
                        await message.channel.send(
                            f"🪤 **Trap triggered!** User **{message.author}** was kicked from the server for sending a message.",
                            delete_after=10,
                        )
                    except discord.HTTPException:
                        pass
                except discord.Forbidden:
                    try:
                        await message.channel.send(
                            f"⚠️ Bot lacks permissions to kick {message.author.mention} (check role hierarchy and 'Kick Members' permission)!",
                            delete_after=10,
                        )
                    except discord.HTTPException:
                        pass


    async def toggle_participant(
        self, message_id: int, user_id: int
    ) -> tuple[str, int, int | None, list[int]]:
        now = int(datetime.now(WARSAW).timestamp())

        async with self.db_lock:
            contest = self.db.execute(
                "SELECT ends_at, max_people, closed FROM contests WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if contest is None:
                return "missing", 0, None, []

            max_people = contest["max_people"]
            if contest["closed"] or contest["ends_at"] <= now:
                winners = self._close_and_draw_locked(message_id, now)
                count = self._participant_count(message_id)
                return "ended", count, max_people, winners

            existing = self.db.execute(
                "SELECT 1 FROM participants WHERE message_id = ? AND user_id = ?",
                (message_id, user_id),
            ).fetchone()

            if existing:
                self.db.execute(
                    "DELETE FROM participants WHERE message_id = ? AND user_id = ?",
                    (message_id, user_id),
                )
                result = "left"
            else:
                count = self._participant_count(message_id)
                if max_people is not None and count >= max_people:
                    return "full", count, max_people, []
                self.db.execute(
                    "INSERT INTO participants (message_id, user_id, joined_at) VALUES (?, ?, ?)",
                    (message_id, user_id, now),
                )
                result = "joined"

            self.db.commit()
            return result, self._participant_count(message_id), max_people, []

    def _close_and_draw_locked(self, message_id: int, now: int) -> list[int]:
        contest = self.db.execute(
            "SELECT winner_count, drawn FROM contests WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if contest is None:
            return []

        if not contest["drawn"]:
            participant_ids = [
                int(row["user_id"])
                for row in self.db.execute(
                    "SELECT user_id FROM participants WHERE message_id = ?",
                    (message_id,),
                ).fetchall()
            ]
            amount = min(int(contest["winner_count"]), len(participant_ids))
            selected = random.SystemRandom().sample(participant_ids, amount)
            self.db.executemany(
                "INSERT INTO winners (message_id, user_id, drawn_at) VALUES (?, ?, ?)",
                [(message_id, user_id, now) for user_id in selected],
            )
            self.db.execute("UPDATE contests SET closed = 1, drawn = 1 WHERE message_id = ?", (message_id,))
            self.db.commit()
        else:
            self.db.execute("UPDATE contests SET closed = 1 WHERE message_id = ?", (message_id,))
            self.db.commit()

        return [
            int(row["user_id"])
            for row in self.db.execute(
                "SELECT user_id FROM winners WHERE message_id = ? ORDER BY user_id",
                (message_id,),
            ).fetchall()
        ]

    def _redraw_locked(self, message_id: int, now: int, new_count: int | None = None) -> list[int]:
        contest = self.db.execute(
            "SELECT winner_count FROM contests WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if contest is None:
            return []

        participant_ids = [
            int(row["user_id"])
            for row in self.db.execute(
                "SELECT user_id FROM participants WHERE message_id = ?",
                (message_id,),
            ).fetchall()
        ]
        if not participant_ids:
            return []

        winner_count = new_count if new_count is not None else int(contest["winner_count"])
        amount = min(winner_count, len(participant_ids))
        selected = random.SystemRandom().sample(participant_ids, amount)

        self.db.execute("DELETE FROM winners WHERE message_id = ?", (message_id,))
        self.db.executemany(
            "INSERT INTO winners (message_id, user_id, drawn_at) VALUES (?, ?, ?)",
            [(message_id, user_id, now) for user_id in selected],
        )
        self.db.execute(
            "UPDATE contests SET closed = 1, drawn = 1, winner_count = ? WHERE message_id = ?",
            (winner_count, message_id),
        )
        self.db.commit()
        return selected

    def _participant_count(self, message_id: int) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS amount FROM participants WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return int(row["amount"])

    async def refresh_message(
        self, message: discord.Message, count: int, max_people: int | None
    ) -> None:
        if not message.embeds:
            return
        embed = message.embeds[0].copy()
        for index, field in enumerate(embed.fields):
            if field.name == "👥 Participants":
                embed.set_field_at(
                    index,
                    name="👥 Participants",
                    value=participants_text(count, max_people),
                    inline=False,
                )
                break
        await message.edit(embed=embed, view=ContestView(self))

    async def mark_message_closed(
        self, message: discord.Message, winner_ids: list[int]
    ) -> None:
        embed = message.embeds[0].copy() if message.embeds else discord.Embed()
        embed.colour = EMBED_COLOR
        embed.description = "**The contest has ended.**"
        await message.edit(embed=embed, view=ClosedContestView())
        
        winners_embed = discord.Embed(
            title="Contest Results",
            colour=EMBED_COLOR,
        )
        
        if winner_ids:
            mentions = "\n".join(f"• <@{user_id}>" for user_id in winner_ids)
            winners_embed.description = f"**Winners:**\n{mentions}"
        else:
            winners_embed.description = "No winners — nobody entered the contest."
        
        try:
            await message.reply(embed=winners_embed, mention_author=False)
        except discord.HTTPException:
            pass


    async def toggle_event_participant(
        self, message_id: int, user_id: int
    ) -> tuple[str, int, int | None]:
        now = int(datetime.now(WARSAW).timestamp())

        async with self.db_lock:
            event = self.db.execute(
                "SELECT starts_at, max_people, started FROM events WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if event is None:
                return "missing", 0, None

            max_people = event["max_people"]
            if event["started"] or event["starts_at"] <= now:
                count = self._event_participant_count(message_id)
                return "started", count, max_people

            existing = self.db.execute(
                "SELECT 1 FROM event_participants WHERE message_id = ? AND user_id = ?",
                (message_id, user_id),
            ).fetchone()

            if existing:
                self.db.execute(
                    "DELETE FROM event_participants WHERE message_id = ? AND user_id = ?",
                    (message_id, user_id),
                )
                result = "left"
            else:
                count = self._event_participant_count(message_id)
                if max_people is not None and count >= max_people:
                    return "full", count, max_people
                self.db.execute(
                    "INSERT INTO event_participants (message_id, user_id, joined_at) VALUES (?, ?, ?)",
                    (message_id, user_id, now),
                )
                result = "joined"

            self.db.commit()
            return result, self._event_participant_count(message_id), max_people

    def _event_participant_count(self, message_id: int) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS amount FROM event_participants WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return int(row["amount"])

    def _start_event_locked(self, message_id: int) -> tuple[sqlite3.Row | None, list[int]]:
        event = self.db.execute(
            "SELECT name, channel_id, guild_id, thread_id FROM events WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if event is None:
            return None, []

        self.db.execute("UPDATE events SET started = 1 WHERE message_id = ?", (message_id,))
        self.db.commit()

        participant_ids = [
            int(row["user_id"])
            for row in self.db.execute(
                "SELECT user_id FROM event_participants WHERE message_id = ?",
                (message_id,),
            ).fetchall()
        ]
        return event, participant_ids

    async def refresh_event_message(
        self, message: discord.Message, count: int, max_people: int | None
    ) -> None:
        if not message.embeds:
            return
        embed = message.embeds[0].copy()
        for index, field in enumerate(embed.fields):
            if field.name == "👥 Registered":
                embed.set_field_at(
                    index,
                    name="👥 Registered",
                    value=participants_text(count, max_people),
                    inline=False,
                )
                break
        await message.edit(embed=embed, view=EventView(self))

    async def mark_event_started(
        self, message: discord.Message, event_data: sqlite3.Row, participant_ids: list[int]
    ) -> None:
        embed = message.embeds[0].copy() if message.embeds else discord.Embed()
        embed.colour = EMBED_COLOR
        embed.description = "**The event has started.**"
        await message.edit(embed=embed, view=ClosedEventView())

        announcement = discord.Embed(
            title="Event Started",
            description=f"The event **{event_data['name']}** has just started!",
            colour=EMBED_COLOR,
        )
        try:
            await message.reply(embed=announcement, mention_author=False)
        except discord.HTTPException:
            pass

        guild = self.bot.get_guild(event_data["guild_id"])
        guild_name = guild.name if guild else "Discord Server"

        dm_embed = discord.Embed(
            title="Event Started!",
            description=f"The event **{event_data['name']}** on server **{guild_name}** has just started.",
            colour=EMBED_COLOR,
        )

        for uid in participant_ids:
            user = self.bot.get_user(uid)
            if user is None:
                try:
                    user = await self.bot.fetch_user(uid)
                except discord.HTTPException:
                    continue
            if user:
                try:
                    await user.send(embed=dm_embed)
                except (discord.Forbidden, discord.HTTPException):
                    pass


    async def toggle_lfg_participant(
        self, message_id: int, user_id: int
    ) -> tuple[str, int, int | None]:
        now = int(datetime.now(WARSAW).timestamp())

        async with self.db_lock:
            lfg_entry = self.db.execute(
                "SELECT starts_at, max_people, closed FROM lfg WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if lfg_entry is None:
                return "missing", 0, None

            max_people = lfg_entry["max_people"]
            if lfg_entry["closed"] or lfg_entry["starts_at"] <= now:
                count = self._lfg_participant_count(message_id)
                return "started", count, max_people

            existing = self.db.execute(
                "SELECT 1 FROM lfg_participants WHERE message_id = ? AND user_id = ?",
                (message_id, user_id),
            ).fetchone()

            if existing:
                self.db.execute(
                    "DELETE FROM lfg_participants WHERE message_id = ? AND user_id = ?",
                    (message_id, user_id),
                )
                result = "left"
            else:
                count = self._lfg_participant_count(message_id)
                if max_people is not None and count >= max_people:
                    return "full", count, max_people
                self.db.execute(
                    "INSERT INTO lfg_participants (message_id, user_id, joined_at) VALUES (?, ?, ?)",
                    (message_id, user_id, now),
                )
                result = "joined"

            self.db.commit()
            return result, self._lfg_participant_count(message_id), max_people

    def _lfg_participant_count(self, message_id: int) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS amount FROM lfg_participants WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return int(row["amount"])

    def _close_lfg_locked(self, message_id: int) -> tuple[sqlite3.Row | None, list[int]]:
        lfg_entry = self.db.execute(
            "SELECT game_name, channel_id, guild_id, host_id FROM lfg WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if lfg_entry is None:
            return None, []

        self.db.execute("UPDATE lfg SET closed = 1 WHERE message_id = ?", (message_id,))
        self.db.commit()

        participant_ids = [
            int(row["user_id"])
            for row in self.db.execute(
                "SELECT user_id FROM lfg_participants WHERE message_id = ?",
                (message_id,),
            ).fetchall()
        ]
        return lfg_entry, participant_ids

    async def refresh_lfg_message(
        self, message: discord.Message, count: int, max_people: int | None
    ) -> None:
        if not message.embeds:
            return
        embed = message.embeds[0].copy()
        for index, field in enumerate(embed.fields):
            if field.name == "👥 Players":
                embed.set_field_at(
                    index,
                    name="👥 Players",
                    value=participants_text(count, max_people),
                    inline=False,
                )
                break
        await message.edit(embed=embed, view=LfgView(self))

    async def mark_lfg_closed(
        self, message: discord.Message, lfg_data: sqlite3.Row, participant_ids: list[int]
    ) -> None:
        embed = message.embeds[0].copy() if message.embeds else discord.Embed()
        embed.colour = LFG_COLOR
        embed.description = "**LFG Match has started or been closed.**"
        await message.edit(embed=embed, view=ClosedLfgView())

        players_mentions = ", ".join(f"<@{uid}>" for uid in participant_ids) if participant_ids else "No registered players."

        announcement = discord.Embed(
            title="🎮 Game Time!",
            description=(
                f"Game: **{lfg_data['game_name']}**\n"
                f"Host: <@{lfg_data['host_id']}>\n\n"
                f"**Party:** {players_mentions}"
            ),
            colour=LFG_COLOR,
        )
        try:
            await message.reply(embed=announcement, mention_author=False)
        except discord.HTTPException:
            pass

        guild = self.bot.get_guild(lfg_data["guild_id"])
        guild_name = guild.name if guild else "Discord Server"

        dm_embed = discord.Embed(
            title="🎮 Game Starting!",
            description=f"The match for **{lfg_data['game_name']}** on server **{guild_name}** is starting now!",
            colour=LFG_COLOR,
        )

        for uid in participant_ids:
            user = self.bot.get_user(uid)
            if user is None:
                try:
                    user = await self.bot.fetch_user(uid)
                except discord.HTTPException:
                    continue
            if user:
                try:
                    await user.send(embed=dm_embed)
                except (discord.Forbidden, discord.HTTPException):
                    pass


    @tasks.loop(seconds=15)
    async def background_checker(self) -> None:
        now = int(datetime.now(WARSAW).timestamp())


        async with self.db_lock:
            expired_contests = self.db.execute(
                "SELECT message_id, channel_id FROM contests "
                "WHERE ends_at <= ? AND (closed = 0 OR drawn = 0)",
                (now,),
            ).fetchall()
            contest_results = [
                (row, self._close_and_draw_locked(row["message_id"], now))
                for row in expired_contests
            ]

        for row, winner_ids in contest_results:
            channel = self.bot.get_channel(row["channel_id"])
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                continue
            try:
                message = await channel.fetch_message(row["message_id"])
                await self.mark_message_closed(message, winner_ids)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue


        async with self.db_lock:
            due_events = self.db.execute(
                "SELECT message_id, channel_id FROM events "
                "WHERE starts_at <= ? AND started = 0",
                (now,),
            ).fetchall()
            event_results = [
                (row["message_id"], row["channel_id"], *self._start_event_locked(row["message_id"]))
                for row in due_events
            ]

        for msg_id, chan_id, event_data, participant_ids in event_results:
            if not event_data:
                continue
            channel = self.bot.get_channel(chan_id)
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                continue
            try:
                message = await channel.fetch_message(msg_id)
                await self.mark_event_started(message, event_data, participant_ids)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue


        async with self.db_lock:
            due_lfg = self.db.execute(
                "SELECT message_id, channel_id FROM lfg "
                "WHERE starts_at <= ? AND closed = 0",
                (now,),
            ).fetchall()
            lfg_results = [
                (row["message_id"], row["channel_id"], *self._close_lfg_locked(row["message_id"]))
                for row in due_lfg
            ]

        for msg_id, chan_id, lfg_data, participant_ids in lfg_results:
            if not lfg_data:
                continue
            channel = self.bot.get_channel(chan_id)
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                continue
            try:
                message = await channel.fetch_message(msg_id)
                await self.mark_lfg_closed(message, lfg_data, participant_ids)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue

        # 4. Reminders
        async with self.db_lock:
            due_reminders = self.db.execute(
                "SELECT * FROM reminders WHERE remind_at <= ? AND delivered = 0",
                (now,),
            ).fetchall()
            if due_reminders:
                self.db.execute(
                    "UPDATE reminders SET delivered = 1 WHERE remind_at <= ? AND delivered = 0",
                    (now,),
                )
                self.db.commit()

        for rem in due_reminders:
            embed = discord.Embed(
                title="⏰ Reminder!",
                description=rem["content"],
                colour=REMINDER_COLOR,
            )
            embed.set_footer(text=f"Reminder ID: #{rem['id']}")

            user = self.bot.get_user(rem["user_id"])
            if user is None:
                try:
                    user = await self.bot.fetch_user(rem["user_id"])
                except discord.HTTPException:
                    pass

            delivered_to_channel = False
            if rem["channel_id"] and not rem["send_dm"]:
                channel = self.bot.get_channel(rem["channel_id"])
                if isinstance(channel, (discord.TextChannel, discord.Thread)):
                    try:
                        await channel.send(content=f"<@{rem['user_id']}>", embed=embed)
                        delivered_to_channel = True
                    except discord.HTTPException:
                        pass

            if not delivered_to_channel and user:
                try:
                    await user.send(embed=embed)
                except (discord.Forbidden, discord.HTTPException):
                    pass

    @background_checker.before_loop
    async def before_background_checker(self) -> None:
        await self.bot.wait_until_ready()



    @app_commands.command(name="help", description="Displays the full command list and help overview.")
    @app_commands.describe(
        visibility="Choose whether the help menu is only visible to you (Private) or everyone (Public)"
    )
    async def help_command(
        self,
        interaction: discord.Interaction,
        visibility: Literal["Only for me (Private)", "Everyone (Public)"] = "Only for me (Private)",
    ) -> None:
        embed = discord.Embed(
            title="📢 Server Organization Bot (Version 1.1)",
            colour=EMBED_COLOR,
        )
        embed.add_field(
            name="🎮 Looking For Group (LFG)",
            value=(
                "`/lfg` - Creates a match signup post with interactive buttons.\n"
                "`/end_lfg` - Closes the LFG post early (host or admin).\n"
                "`/lfg_list` - Displays the registered players for a game.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="⏰ Personal Reminders",
            value=(
                "`/remind` - Sets a reminder via DM or channel mention.\n"
                "`/reminders` - Lists all your active scheduled reminders.\n"
                "`/cancel_reminder` - Cancels a reminder by its ID.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="📋 To-Do Task Board",
            value=(
                "`/todo add` - Adds a new task to the server board.\n"
                "`/todo list` - Displays the server task board.\n"
                "`/todo done` - Toggles task status between Completed and To Do.\n"
                "`/todo assign` - Assigns a task to yourself or someone else.\n"
                "`/todo delete` - Removes a task from the board.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎉 Contests & Events",
            value=(
                "`/contest` - Creates a contest with automated winner drawing.\n"
                "`/reroll` | `/end_contest` | `/participants` - Contest controls.\n"
                "`/event` | `/start_event` | `/list` | `/edit_event` - Server events.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="🪤 Trap Channels (Admin Only)",
            value=(
                "`/trap set` - Designates a channel as a trap (message = kick).\n"
                "`/trap remove` - Disables the trap status on a channel.\n"
                "`/trap list` - Displays all active trap channels.\n" 
            ),
            inline=False,
        )
        embed.set_footer(
            text="Version 1.1 | Organization Bot | Developer: karoltoooo11\n"
                 "I'm kinda proud of myself! If the bot has any problems or You want to give feedback, please write to me! I will be very happy!"
                 "Bot is sinchronized with time in Warsaw, Poland (CET/CEST). Also all translations are done moslty by Google Translator, so if You find any mistakes, please report them to me. Thank You again!"
        )
        is_ephemeral = (visibility == "Only for me (Private)")
        await interaction.response.send_message(embed=embed, ephemeral=is_ephemeral)






    @app_commands.command(name="remind", description="Sets a personal reminder.")
    @app_commands.describe(
        when="When to remind You? (e.g. 15m, 2h, 1d, 20:30, 31.12.2026 20:00)",
        content="What should I remind You about?",
        delivery="Where to send the reminder? (Default: Direct Message)",
    )
    async def remind(
        self,
        interaction: discord.Interaction,
        when: str,
        content: str,
        delivery: Literal["Direct Message (DM)", "Channel Mention"] = "Direct Message (DM)",
    ) -> None:
        try:
            remind_dt = parse_reminder_time(when)
        except ValueError as error:
            await interaction.response.send_message(f"Time format error: {error}", ephemeral=True)
            return

        if remind_dt <= datetime.now(WARSAW):
            await interaction.response.send_message(
                "Reminder time must be in the future.", ephemeral=True
            )
            return

        remind_ts = int(remind_dt.timestamp())
        send_dm = 1 if delivery == "Direct Message (DM)" else 0
        channel_id = interaction.channel_id if not send_dm else None

        async with self.db_lock:
            cursor = self.db.execute(
                """
                INSERT INTO reminders
                    (user_id, channel_id, guild_id, remind_at, content, send_dm)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    interaction.user.id,
                    channel_id,
                    interaction.guild_id,
                    remind_ts,
                    content,
                    send_dm,
                ),
            )
            self.db.commit()
            reminder_id = cursor.lastrowid

        embed = discord.Embed(
            title="⏰ Reminder Scheduled!",
            description=(
                f"**Content:** {content}\n"
                f"**Time:** <t:{remind_ts}:F> (<t:{remind_ts}:R>)\n"
                f"**Delivery:** {delivery}"
            ),
            colour=REMINDER_COLOR,
        )
        embed.set_footer(text=f"Reminder ID: #{reminder_id} (use /cancel_reminder to remove)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="reminders", description="Displays the list of your active reminders.")
    async def reminders(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        now = int(datetime.now(WARSAW).timestamp())

        async with self.db_lock:
            rows = self.db.execute(
                "SELECT * FROM reminders WHERE user_id = ? AND remind_at > ? AND delivered = 0 ORDER BY remind_at ASC",
                (interaction.user.id, now),
            ).fetchall()

        if not rows:
            await interaction.followup.send("You have no active scheduled reminders.", ephemeral=True)
            return

        lines = [
            f"**#{row['id']}** | <t:{row['remind_at']}:R> (<t:{row['remind_at']}:t>)\n"
            f"└ 📝 {row['content'][:80]}{'...' if len(row['content']) > 80 else ''}"
            for row in rows
        ]

        embed = discord.Embed(
            title=f"⏰ Your Active Reminders ({len(rows)})",
            description="\n\n".join(lines),
            colour=REMINDER_COLOR,
        )
        embed.set_footer(text="To remove a reminder, use /cancel_reminder <id> \n" \
        "Version 1.1 | Organization Bot | Developer: karoltoooo11\n")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="cancel_reminder", description="Cancels a scheduled reminder.")
    @app_commands.describe(reminder_id="Reminder ID (find it using /reminders)")
    async def cancel_reminder(self, interaction: discord.Interaction, reminder_id: int) -> None:
        async with self.db_lock:
            row = self.db.execute(
                "SELECT user_id FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()

            if row is None:
                await interaction.response.send_message("No reminder found with the specified ID.", ephemeral=True)
                return

            if row["user_id"] != interaction.user.id:
                await interaction.response.send_message("This is not your reminder!", ephemeral=True)
                return

            self.db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            self.db.commit()

        await interaction.response.send_message(f"Successfully deleted reminder **#{reminder_id}**.", ephemeral=True)





    todo_group = app_commands.Group(name="todo", description="Server to-do task board management.")

    @todo_group.command(name="add", description="Adds a new task to the server to-do board.")
    @app_commands.describe(
        content="Task description",
        priority="Priority level",
    )
    async def todo_add(
        self,
        interaction: discord.Interaction,
        content: str,
        priority: Literal["🔴 High", "🟡 Medium", "🟢 Low"] = "🟡 Medium",
    ) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        now = int(datetime.now(WARSAW).timestamp())

        async with self.db_lock:
            cursor = self.db.execute(
                """
                INSERT INTO todos
                    (guild_id, creator_id, content, priority, status, created_at)
                VALUES (?, ?, ?, ?, 'todo', ?)
                """,
                (interaction.guild_id, interaction.user.id, content, priority, now),
            )
            self.db.commit()
            todo_id = cursor.lastrowid

        embed = discord.Embed(
            title="📋 New Task Added!",
            description=(
                f"**ID:** `#{todo_id}`\n"
                f"**Content:** {content}\n"
                f"**Priority:** {priority}\n"
                f"**Added by:** {interaction.user.mention}"
            ),
            colour=TODO_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @todo_group.command(name="list", description="Displays the server to-do board.")
    @app_commands.describe(filter="Filter tasks by status")
    async def todo_list(
        self,
        interaction: discord.Interaction,
        filter: Literal["All", "To Do", "Done"] = "To Do",
    ) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer()

        query = "SELECT * FROM todos WHERE guild_id = ?"
        params: list[object] = [interaction.guild_id]

        if filter == "To Do":
            query += " AND status = 'todo'"
        elif filter == "Done":
            query += " AND status = 'done'"

        query += " ORDER BY CASE priority WHEN '🔴 High' THEN 1 WHEN '🟡 Medium' THEN 2 ELSE 3 END, id DESC"

        async with self.db_lock:
            rows = self.db.execute(query, params).fetchall()

        if not rows:
            await interaction.followup.send(f"No tasks found in category **{filter}**.")
            return

        lines: list[str] = []
        for r in rows:
            icon = "✅" if r["status"] == "done" else "⬜"
            assigned_text = f" -> <@{r['assigned_id']}>" if r["assigned_id"] else ""
            content_display = f"~~{r['content']}~~" if r["status"] == "done" else r["content"]
            lines.append(f"{icon} `#{r['id']}` **[{r['priority']}]** {content_display}{assigned_text}")

        embed = discord.Embed(
            title=f"📋 Server Task Board ({filter})",
            description="\n".join(lines[:25]),
            colour=TODO_COLOR,
        )
        if len(rows) > 25:
            embed.set_footer(text=f"Showing 25 of {len(rows)} tasks.")
        else:
            embed.set_footer(text="Manage: /todo done <id> | /todo assign <id> | /todo delete <id>")
        embed.set_footer(text="To remove a reminder, use /cancel_reminder <id> \n" \
        "Version 1.1 | Organization Bot | Developer: karoltoooo11\n")
            

        await interaction.followup.send(embed=embed)

    @todo_group.command(name="done", description="Toggles a task as completed.")
    @app_commands.describe(task_id="ID of the task to toggle")
    async def todo_done(self, interaction: discord.Interaction, task_id: int) -> None:
        now = int(datetime.now(WARSAW).timestamp())

        async with self.db_lock:
            row = self.db.execute(
                "SELECT * FROM todos WHERE id = ? AND guild_id = ?",
                (task_id, interaction.guild_id),
            ).fetchone()

            if not row:
                await interaction.response.send_message("Task with this ID was not found on this server.", ephemeral=True)
                return

            new_status = "todo" if row["status"] == "done" else "done"
            completed_at = now if new_status == "done" else None

            self.db.execute(
                "UPDATE todos SET status = ?, completed_at = ? WHERE id = ?",
                (new_status, completed_at, task_id),
            )
            self.db.commit()

        status_txt = "completed! ✅" if new_status == "done" else "reopened as To Do! ⬜"
        await interaction.response.send_message(f"Task `#{task_id}` marked as {status_txt}")

    @todo_group.command(name="assign", description="Assigns a task to yourself or a server member.")
    @app_commands.describe(
        task_id="Task ID",
        user="Member to assign the task to (defaults to you)",
    )
    async def todo_assign(
        self,
        interaction: discord.Interaction,
        task_id: int,
        user: discord.User | discord.Member | None = None,
    ) -> None:
        target_user = user or interaction.user

        async with self.db_lock:
            row = self.db.execute(
                "SELECT * FROM todos WHERE id = ? AND guild_id = ?",
                (task_id, interaction.guild_id),
            ).fetchone()

            if not row:
                await interaction.response.send_message("Task with this ID was not found on this server.", ephemeral=True)
                return

            self.db.execute(
                "UPDATE todos SET assigned_id = ? WHERE id = ?",
                (target_user.id, task_id),
            )
            self.db.commit()

        await interaction.response.send_message(f"Task `#{task_id}` has been assigned to {target_user.mention} ✋")

    @todo_group.command(name="delete", description="Deletes a task from the board.")
    @app_commands.describe(task_id="ID of the task to delete")
    async def todo_delete(self, interaction: discord.Interaction, task_id: int) -> None:
        async with self.db_lock:
            row = self.db.execute(
                "SELECT * FROM todos WHERE id = ? AND guild_id = ?",
                (task_id, interaction.guild_id),
            ).fetchone()

            if not row:
                await interaction.response.send_message("Task with this ID was not found on this server.", ephemeral=True)
                return

            is_creator = row["creator_id"] == interaction.user.id
            is_admin = interaction.permissions.manage_guild if interaction.permissions else False

            if not (is_creator or is_admin):
                await interaction.response.send_message(
                    "Only the task creator or a server administrator can delete this task.", ephemeral=True
                )
                return

            self.db.execute("DELETE FROM todos WHERE id = ?", (task_id,))
            self.db.commit()

        await interaction.response.send_message(f"Deleted task `#{task_id}` from the board.")













    trap_group = app_commands.Group(name="trap", description="Trap channel controls (automatic kick on message).")

    @trap_group.command(name="set", description="Designates a channel as a trap (message = instant kick).")
    @app_commands.describe(channel="Text channel to set as a trap (defaults to current)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def trap_set(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        target_channel = channel or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message("You must select a standard text channel.", ephemeral=True)
            return

        now = int(datetime.now(WARSAW).timestamp())

        async with self.db_lock:
            self.db.execute(
                """
                INSERT OR REPLACE INTO trap_channels (channel_id, guild_id, created_at, set_by)
                VALUES (?, ?, ?, ?)
                """,
                (target_channel.id, interaction.guild_id, now, interaction.user.id),
            )
            self.db.commit()
            self.trap_channels.add(target_channel.id)

        embed = discord.Embed(
            title="🪤 Trap Channel Activated!",
            description=(
                f"Channel {target_channel.mention} is now active as a **trap**.\n\n"
                f"⚠️ **Any member** who sends a message here will be **instantly kicked from the server**, and their message deleted.\n\n"
                f"*(Server administrators and the server owner are immune to kicks).*"
            ),
            colour=TRAP_COLOR,
        )
        embed.set_footer(text=f"Activated by: {interaction.user}")
        embed.set_footer(text="Version 1.1 | Organization Bot | Developer: karoltoooo11\n")
        await interaction.response.send_message(embed=embed)

    @trap_group.command(name="remove", description="Disables the trap on the specified channel.")
    @app_commands.describe(channel="Text channel to remove the trap from (defaults to current)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def trap_remove(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        target_channel = channel or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message("You must select a standard text channel.", ephemeral=True)
            return

        async with self.db_lock:
            row = self.db.execute(
                "SELECT 1 FROM trap_channels WHERE channel_id = ? AND guild_id = ?",
                (target_channel.id, interaction.guild_id),
            ).fetchone()

            if not row:
                await interaction.response.send_message(f"Channel {target_channel.mention} is not designated as a trap.", ephemeral=True)
                return

            self.db.execute("DELETE FROM trap_channels WHERE channel_id = ?", (target_channel.id,))
            self.db.commit()
            self.trap_channels.discard(target_channel.id)

        await interaction.response.send_message(f"✅ Disabled trap on channel {target_channel.mention}.")

    @trap_group.command(name="list", description="Lists all active trap channels on the server.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def trap_list(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        async with self.db_lock:
            rows = self.db.execute(
                "SELECT channel_id, created_at, set_by FROM trap_channels WHERE guild_id = ?",
                (interaction.guild_id,),
            ).fetchall()

        if not rows:
            await interaction.followup.send("There are no trap channels configured on this server.", ephemeral=True)
            return

        lines = [
            f"• <#{row['channel_id']}> (`{row['channel_id']}`) — set <t:{row['created_at']}:R> by <@{row['set_by']}>"
            for row in rows
        ]

        embed = discord.Embed(
            title=f"🪤 Active Trap Channels ({len(rows)})",
            description="\n".join(lines),
            colour=TRAP_COLOR,
        )
        embed.set_footer(text="Use /trap remove to disable a trap channel")
        await interaction.followup.send(embed=embed, ephemeral=True)


    @app_commands.command(name="lfg", description="Creates a matchmaking signup post for any game.")
    @app_commands.describe(
        game="Game name (e.g. League of Legends, CS2, Valorant)",
        date="Start date/time in DD.MM.YYYY HH:MM format (e.g. 01.09.2026 20:00)",
        slots="Number of players needed (e.g. 4 or 'none')",
    )
    @app_commands.guild_only()
    async def lfg(
        self,
        interaction: discord.Interaction,
        game: str,
        date: str,
        slots: str = "none",
    ) -> None:
        try:
            start_date = parse_end_date(date)
            max_people = parse_limit(slots)
        except ValueError as error:
            await interaction.response.send_message(f"Error: {error}", ephemeral=True)
            return

        if start_date <= datetime.now(WARSAW):
            await interaction.response.send_message(
                "Game start date must be in the future.", ephemeral=True
            )
            return

        start_timestamp = int(start_date.timestamp())
        embed = discord.Embed(
            title=f"🎮 Looking for Group: {game}",
            description="Click the button below to join or leave the party!",
            colour=LFG_COLOR,
        )
        embed.add_field(name="⏰ Start Time", value=f"<t:{start_timestamp}:F>\n<t:{start_timestamp}:R>", inline=False)
        embed.add_field(name="👥 Players", value=participants_text(0, max_people), inline=False)
        embed.set_footer(text=f"Host: {interaction.user}")
        embed.set_footer(text="Version 1.1 | Organization Bot | Developer: karoltoooo11\n")

        await interaction.response.send_message(
            embed=embed,
            view=LfgView(self),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        message = await interaction.original_response()

        async with self.db_lock:
            self.db.execute(
                """
                INSERT INTO lfg
                    (message_id, channel_id, guild_id, host_id, game_name, starts_at, max_people)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.channel.id,
                    interaction.guild_id,
                    interaction.user.id,
                    game,
                    start_timestamp,
                    max_people,
                ),
            )
            self.db.commit()

    @app_commands.command(name="end_lfg", description="Ends the LFG party early (host or administrator).")
    @app_commands.describe(message_id="ID of the LFG message (or link)")
    @app_commands.guild_only()
    async def end_lfg(self, interaction: discord.Interaction, message_id: str) -> None:
        await interaction.response.defer(ephemeral=True)

        cleaned_id = message_id.strip().split("/")[-1]
        if not cleaned_id.isdigit():
            await interaction.followup.send("Please provide a valid message ID (digits only).", ephemeral=True)
            return

        msg_id = int(cleaned_id)

        async with self.db_lock:
            lfg_entry = self.db.execute(
                "SELECT * FROM lfg WHERE message_id = ?",
                (msg_id,),
            ).fetchone()

            if lfg_entry is None:
                await interaction.followup.send("No LFG post found with this message ID.", ephemeral=True)
                return

            if lfg_entry["closed"]:
                await interaction.followup.send("This LFG post has already been closed.", ephemeral=True)
                return

            is_host = interaction.user.id == lfg_entry["host_id"]
            is_admin = interaction.permissions.manage_guild if interaction.permissions else False

            if not (is_host or is_admin):
                await interaction.followup.send(
                    "Only the party host or a server administrator can end this LFG post.",
                    ephemeral=True,
                )
                return

            lfg_data, participant_ids = self._close_lfg_locked(msg_id)

        channel = self.bot.get_channel(lfg_entry["channel_id"])
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                channel = await self.bot.fetch_channel(lfg_entry["channel_id"])
            except discord.HTTPException:
                channel = None

        if channel and lfg_data:
            try:
                msg = await channel.fetch_message(msg_id)
                await self.mark_lfg_closed(msg, lfg_data, participant_ids)
            except discord.HTTPException:
                pass

        await interaction.followup.send("LFG post has been closed successfully!", ephemeral=True)

    @app_commands.command(name="lfg_list", description="Displays the list of players signed up for a match.")
    @app_commands.describe(message_id="ID of the LFG message (or link)")
    @app_commands.guild_only()
    async def lfg_list(self, interaction: discord.Interaction, message_id: str) -> None:
        await interaction.response.defer(ephemeral=True)

        cleaned_id = message_id.strip().split("/")[-1]
        if not cleaned_id.isdigit():
            await interaction.followup.send("Please provide a valid message ID (digits only).", ephemeral=True)
            return

        msg_id = int(cleaned_id)

        async with self.db_lock:
            lfg_entry = self.db.execute(
                "SELECT game_name, max_people, host_id FROM lfg WHERE message_id = ?",
                (msg_id,),
            ).fetchone()

            if lfg_entry is None:
                await interaction.followup.send("No LFG post found with this message ID.", ephemeral=True)
                return

            rows = self.db.execute(
                "SELECT user_id, joined_at FROM lfg_participants WHERE message_id = ? ORDER BY joined_at ASC",
                (msg_id,),
            ).fetchall()

        if not rows:
            await interaction.followup.send(
                f"No registered players for **{lfg_entry['game_name']}** (Host: <@{lfg_entry['host_id']}>).",
                ephemeral=True,
            )
            return

        total = len(rows)
        limit_text = f" / {lfg_entry['max_people']}" if lfg_entry["max_people"] is not None else ""

        lines = [
            f"{i}. <@{row['user_id']}> (`{row['user_id']}`) — joined: <t:{row['joined_at']}:R>"
            for i, row in enumerate(rows, 1)
        ]

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_len = 0
        for line in lines:
            if current_len + len(line) + 1 > 3500:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_len = len(line)
            else:
                current_chunk.append(line)
                current_len += len(line) + 1
        if current_chunk:
            chunks.append("\n".join(current_chunk))

        for idx, chunk in enumerate(chunks):
            title = (
                f"Party for {lfg_entry['game_name']} ({total}{limit_text})"
                if idx == 0
                else f"Party for {lfg_entry['game_name']} (cont.)"
            )
            embed = discord.Embed(
                title=title,
                description=f"**Host:** <@{lfg_entry['host_id']}>\n\n**Players:**\n{chunk}",
                colour=LFG_COLOR,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)


    @app_commands.command(name="contest", description="Creates a new contest.")
    @app_commands.describe(
        name="Name of the contest",
        end_date="End date in DD.MM.YYYY HH:MM format",
        prize="Prize for the winner",
        limit="Maximum participant limit or 'none'",
        winner_count="Number of winners to draw (default 1)",
    )
    @app_commands.guild_only()
    async def contest(
        self,
        interaction: discord.Interaction,
        name: str,
        end_date: str,
        prize: str,
        limit: str = "none",
        winner_count: app_commands.Range[int, 1, 20] = 1,
    ) -> None:
        try:
            dt = parse_end_date(end_date)
            max_people = parse_limit(limit)
        except ValueError as error:
            await interaction.response.send_message(f"Error: {error}", ephemeral=True)
            return

        if max_people is not None and winner_count > max_people:
            await interaction.response.send_message(
                "Winner count cannot be greater than the participant limit.",
                ephemeral=True,
            )
            return

        if dt <= datetime.now(WARSAW):
            await interaction.response.send_message(
                "End date must be in the future.", ephemeral=True
            )
            return

        end_timestamp = int(dt.timestamp())
        embed = discord.Embed(
            title=f"🎉 Contest: {name}",
            description="Click the button below to join or leave.",
            colour=EMBED_COLOR,
        )
        embed.add_field(name="🏆 Prize", value=prize, inline=False)
        embed.add_field(name="⏰ End Time", value=f"<t:{end_timestamp}:F>\n<t:{end_timestamp}:R>", inline=False)
        embed.add_field(name="👥 Participants", value=participants_text(0, max_people), inline=False)
        embed.add_field(name="🎲 Number of winners", value=str(winner_count), inline=False)
        embed.set_footer(text=f"Host: {interaction.user}")
        embed.set_footer(text="Version 1.1 | Organization Bot | Developer: karoltoooo11\n")

        await interaction.response.send_message(
            embed=embed,
            view=ContestView(self),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        message = await interaction.original_response()

        async with self.db_lock:
            self.db.execute(
                """
                INSERT INTO contests
                    (message_id, channel_id, guild_id, name, prize, ends_at, max_people, winner_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.channel.id,
                    interaction.guild_id,
                    name,
                    prize,
                    end_timestamp,
                    max_people,
                    winner_count,
                ),
            )
            self.db.commit()

    @app_commands.command(name="end_contest", description="Manually ends a contest and draws winners.")
    @app_commands.describe(message_id="Contest message ID (or message link)")
    @app_commands.guild_only()
    async def end_contest(self, interaction: discord.Interaction, message_id: str) -> None:
        await interaction.response.defer(ephemeral=True)

        cleaned_id = message_id.strip().split("/")[-1]
        if not cleaned_id.isdigit():
            await interaction.followup.send("Please provide a valid message ID (digits only).", ephemeral=True)
            return

        msg_id = int(cleaned_id)
        now = int(datetime.now(WARSAW).timestamp())

        async with self.db_lock:
            contest = self.db.execute(
                "SELECT channel_id, closed FROM contests WHERE message_id = ?",
                (msg_id,),
            ).fetchone()

            if contest is None:
                await interaction.followup.send("No contest found with this message ID.", ephemeral=True)
                return

            if contest["closed"]:
                await interaction.followup.send("This contest has already ended.", ephemeral=True)
                return

            winner_ids = self._close_and_draw_locked(msg_id, now)

        channel = self.bot.get_channel(contest["channel_id"])
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                channel = await self.bot.fetch_channel(contest["channel_id"])
            except discord.HTTPException:
                channel = None

        if channel:
            try:
                msg = await channel.fetch_message(msg_id)
                await self.mark_message_closed(msg, winner_ids)
            except discord.HTTPException:
                pass

        await interaction.followup.send("Contest has ended and winners have been drawn.", ephemeral=True)

    @app_commands.command(name="reroll", description="Redraws the winners of a concluded contest.")
    @app_commands.describe(
        message_id="Contest message ID (or message link)",
        winner_count="Optional new winner count (defaults to original contest config)",
    )
    @app_commands.guild_only()
    async def reroll(
        self,
        interaction: discord.Interaction,
        message_id: str,
        winner_count: app_commands.Range[int, 1, 20] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        cleaned_id = message_id.strip().split("/")[-1]
        if not cleaned_id.isdigit():
            await interaction.followup.send("Please provide a valid message ID (digits only).", ephemeral=True)
            return

        msg_id = int(cleaned_id)
        now = int(datetime.now(WARSAW).timestamp())

        async with self.db_lock:
            contest = self.db.execute(
                "SELECT channel_id FROM contests WHERE message_id = ?",
                (msg_id,),
            ).fetchone()

            if contest is None:
                await interaction.followup.send("No contest found with this message ID.", ephemeral=True)
                return

            winner_ids = self._redraw_locked(msg_id, now, winner_count)

        channel = self.bot.get_channel(contest["channel_id"])
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                channel = await self.bot.fetch_channel(contest["channel_id"])
            except discord.HTTPException:
                channel = None

        if channel:
            try:
                msg = await channel.fetch_message(msg_id)
                closed_embed = msg.embeds[0].copy() if msg.embeds else discord.Embed()
                closed_embed.colour = EMBED_COLOR
                closed_embed.description = "**The contest has ended.**"
                await msg.edit(embed=closed_embed, view=ClosedContestView())

                embed = discord.Embed(
                    title="Reroll Results",
                    colour=EMBED_COLOR,
                )
                if winner_ids:
                    mentions = "\n".join(f"• <@{user_id}>" for user_id in winner_ids)
                    embed.description = f"**New winners:**\n{mentions}"
                else:
                    embed.description = "No participants to draw from."

                await msg.reply(embed=embed, mention_author=False)
            except discord.HTTPException:
                pass

        await interaction.followup.send("Winners have been rerolled.", ephemeral=True)

    @app_commands.command(name="participants", description="Displays the list of contest participants.")
    @app_commands.describe(message_id="Contest message ID (or message link)")
    @app_commands.guild_only()
    async def participants(self, interaction: discord.Interaction, message_id: str) -> None:
        await interaction.response.defer(ephemeral=True)

        cleaned_id = message_id.strip().split("/")[-1]
        if not cleaned_id.isdigit():
            await interaction.followup.send("Please provide a valid message ID (digits only).", ephemeral=True)
            return

        msg_id = int(cleaned_id)

        async with self.db_lock:
            contest = self.db.execute(
                "SELECT name, max_people FROM contests WHERE message_id = ?",
                (msg_id,),
            ).fetchone()

            if contest is None:
                await interaction.followup.send("No contest found with this message ID.", ephemeral=True)
                return

            rows = self.db.execute(
                "SELECT user_id, joined_at FROM participants WHERE message_id = ? ORDER BY joined_at ASC",
                (msg_id,),
            ).fetchall()

        if not rows:
            await interaction.followup.send(
                f"No registered participants in contest **{contest['name']}**.", ephemeral=True
            )
            return

        total = len(rows)
        limit_text = f" / {contest['max_people']}" if contest["max_people"] is not None else ""

        lines = [
            f"{i}. <@{row['user_id']}> (`{row['user_id']}`) — joined: <t:{row['joined_at']}:R>"
            for i, row in enumerate(rows, 1)
        ]

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_len = 0
        for line in lines:
            if current_len + len(line) + 1 > 3500:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_len = len(line)
            else:
                current_chunk.append(line)
                current_len += len(line) + 1
        if current_chunk:
            chunks.append("\n".join(current_chunk))

        for idx, chunk in enumerate(chunks):
            title = (
                f"Participants list: {contest['name']} ({total}{limit_text})"
                if idx == 0
                else f"Participants list: {contest['name']} (cont.)"
            )
            embed = discord.Embed(
                title=title,
                description=chunk,
                colour=EMBED_COLOR,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

 
    @app_commands.command(name="event", description="Creates a new event with signups.")
    @app_commands.describe(
        name="Event name",
        date="Start date in DD.MM.YYYY HH:MM format",
        description="Event description, rules, schedule",
        limit="Maximum participant limit or 'none'",
        create_thread="Whether to create an automatic discussion thread under the event",
    )
    @app_commands.guild_only()
    async def event(
        self,
        interaction: discord.Interaction,
        name: str,
        date: str,
        description: str,
        limit: str = "none",
        create_thread: bool = False,
    ) -> None:
        try:
            start_date = parse_end_date(date)
            max_people = parse_limit(limit)
        except ValueError as error:
            await interaction.response.send_message(f"Error: {error}", ephemeral=True)
            return

        if start_date <= datetime.now(WARSAW):
            await interaction.response.send_message(
                "Start date must be in the future.", ephemeral=True
            )
            return

        end_timestamp = int(start_date.timestamp())
        embed = discord.Embed(
            title=f"📅 Event: {name}",
            description="Click the button below to sign up.",
            colour=EMBED_COLOR,
        )
        embed.add_field(name="⏰ Start Time", value=f"<t:{end_timestamp}:F>\n<t:{end_timestamp}:R>", inline=False)
        embed.add_field(name="📝 Description", value=description, inline=False)
        embed.add_field(name="👥 Registered", value=participants_text(0, max_people), inline=False)
        embed.set_footer(text=f"Host: {interaction.user}")
        embed.set_footer(text="Version 1.1 | Organization Bot | Developer: karoltoooo11\n")

        await interaction.response.send_message(
            embed=embed,
            view=EventView(self),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        message = await interaction.original_response()

        thread_id = None
        if create_thread and isinstance(interaction.channel, discord.TextChannel):
            try:
                thread = await message.create_thread(name=f"Discussion: {name[:50]}", auto_archive_duration=1440)
                thread_id = thread.id
            except discord.HTTPException:
                pass

        async with self.db_lock:
            self.db.execute(
                """
                INSERT INTO events
                    (message_id, channel_id, guild_id, name, description, starts_at, max_people, thread_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.channel.id,
                    interaction.guild_id,
                    name,
                    description,
                    end_timestamp,
                    max_people,
                    thread_id,
                ),
            )
            self.db.commit()

    @app_commands.command(name="start_event", description="Manually starts an event early and sends DMs to participants.")
    @app_commands.describe(message_id="Event message ID (or link)")
    @app_commands.guild_only()
    async def start_event(self, interaction: discord.Interaction, message_id: str) -> None:
        await interaction.response.defer(ephemeral=True)

        cleaned_id = message_id.strip().split("/")[-1]
        if not cleaned_id.isdigit():
            await interaction.followup.send("Please provide a valid message ID (digits only).", ephemeral=True)
            return

        msg_id = int(cleaned_id)

        async with self.db_lock:
            event = self.db.execute(
                "SELECT channel_id, started FROM events WHERE message_id = ?",
                (msg_id,),
            ).fetchone()

            if event is None:
                await interaction.followup.send("No event found with this message ID.", ephemeral=True)
                return

            if event["started"]:
                await interaction.followup.send("This event has already started.", ephemeral=True)
                return

            event_data, participant_ids = self._start_event_locked(msg_id)

        channel = self.bot.get_channel(event["channel_id"])
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                channel = await self.bot.fetch_channel(event["channel_id"])
            except discord.HTTPException:
                channel = None

        if channel and event_data:
            try:
                msg = await channel.fetch_message(msg_id)
                await self.mark_event_started(msg, event_data, participant_ids)
            except discord.HTTPException:
                pass

        await interaction.followup.send(
            f"Event has been started successfully! Sent notifications to {len(participant_ids)} registered members.",
            ephemeral=True,
        )

    @app_commands.command(name="list", description="Displays the list of members registered for the event.")
    @app_commands.describe(message_id="Event message ID (or link)")
    @app_commands.guild_only()
    async def list_command(self, interaction: discord.Interaction, message_id: str) -> None:
        await interaction.response.defer(ephemeral=True)

        cleaned_id = message_id.strip().split("/")[-1]
        if not cleaned_id.isdigit():
            await interaction.followup.send("Please provide a valid message ID (digits only).", ephemeral=True)
            return

        msg_id = int(cleaned_id)

        async with self.db_lock:
            event = self.db.execute(
                "SELECT name, max_people FROM events WHERE message_id = ?",
                (msg_id,),
            ).fetchone()

            if event is None:
                await interaction.followup.send("No event found with this message ID.", ephemeral=True)
                return

            rows = self.db.execute(
                "SELECT user_id, joined_at FROM event_participants WHERE message_id = ? ORDER BY joined_at ASC",
                (msg_id,),
            ).fetchall()

        if not rows:
            await interaction.followup.send(
                f"No registered members for event **{event['name']}**.", ephemeral=True
            )
            return

        total = len(rows)
        limit_text = f" / {event['max_people']}" if event["max_people"] is not None else ""

        lines = [
            f"{i}. <@{row['user_id']}> (`{row['user_id']}`) — joined: <t:{row['joined_at']}:R>"
            for i, row in enumerate(rows, 1)
        ]

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_len = 0
        for line in lines:
            if current_len + len(line) + 1 > 3500:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_len = len(line)
            else:
                current_chunk.append(line)
                current_len += len(line) + 1
        if current_chunk:
            chunks.append("\n".join(current_chunk))

        for idx, chunk in enumerate(chunks):
            title = (
                f"Registered for event: {event['name']} ({total}{limit_text})"
                if idx == 0
                else f"Registered for event: {event['name']} (cont.)"
            )
            embed = discord.Embed(
                title=title,
                description=chunk,
                colour=EMBED_COLOR,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="edit_event", description="Edits parameters of an existing event.")
    @app_commands.describe(
        message_id="Event message ID (or link)",
        new_name="New event name (optional)",
        new_date="New date in DD.MM.YYYY HH:MM format (optional)",
        new_description="New event description (optional)",
        new_limit="New participant limit or 'none' (optional)",
    )
    @app_commands.guild_only()
    async def edit_event(
        self,
        interaction: discord.Interaction,
        message_id: str,
        new_name: str | None = None,
        new_date: str | None = None,
        new_description: str | None = None,
        new_limit: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        cleaned_id = message_id.strip().split("/")[-1]
        if not cleaned_id.isdigit():
            await interaction.followup.send("Please provide a valid message ID (digits only).", ephemeral=True)
            return

        msg_id = int(cleaned_id)

        async with self.db_lock:
            event = self.db.execute(
                "SELECT * FROM events WHERE message_id = ?",
                (msg_id,),
            ).fetchone()

            if event is None:
                await interaction.followup.send("Wrong message ID provided.", ephemeral=True)
                return

            if event["started"]:
                await interaction.followup.send("Cannot edit an event that has already started.", ephemeral=True)
                return

            name = new_name if new_name is not None else event["name"]
            description = new_description if new_description is not None else event["description"]

            if new_date is not None:
                try:
                    dt = parse_end_date(new_date)
                    if dt <= datetime.now(WARSAW):
                        await interaction.followup.send("New date must be in the future.", ephemeral=True)
                        return
                    starts_at = int(dt.timestamp())
                except ValueError as error:
                    await interaction.followup.send(f"Date error: {error}", ephemeral=True)
                    return
            else:
                starts_at = event["starts_at"]

            if new_limit is not None:
                try:
                    max_people = parse_limit(new_limit)
                except ValueError as error:
                    await interaction.followup.send(f"Limit error: {error}", ephemeral=True)
                    return
            else:
                max_people = event["max_people"]

            self.db.execute(
                """
                UPDATE events
                SET name = ?, description = ?, starts_at = ?, max_people = ?
                WHERE message_id = ?
                """,
                (name, description, starts_at, max_people, msg_id),
            )
            self.db.commit()
            count = self._event_participant_count(msg_id)

        channel = self.bot.get_channel(event["channel_id"])
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                channel = await self.bot.fetch_channel(event["channel_id"])
            except discord.HTTPException:
                channel = None

        if channel:
            try:
                msg = await channel.fetch_message(msg_id)
                embed = discord.Embed(
                    title=f"📅 Event: {name}",
                    description="Click the button below to sign up.",
                    colour=EMBED_COLOR,
                )
                embed.add_field(name="⏰ Start Time", value=f"<t:{starts_at}:F>\n<t:{starts_at}:R>", inline=False)
                embed.add_field(name="📝 Description", value=description, inline=False)
                embed.add_field(name="👥 Registered", value=participants_text(count, max_people), inline=False)
                if msg.embeds and msg.embeds[0].footer: 
                    embed.set_footer(text=msg.embeds[0].footer.text)
                embed.set_footer(text="Version 1.1 | Organization Bot | Developer: karoltoooo11\n")
                await msg.edit(embed=embed, view=EventView(self))
            except discord.HTTPException:
                pass

        await interaction.followup.send("Event has been updated successfully.", ephemeral=True)

    @trap_set.error
    @trap_remove.error
    @trap_list.error
    async def commands_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            text = "You don't have permission needed to execute this command. ('Manage Server')"
        else:
            text = "An error occurred while executing the command. Chceck logs."

        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ContestCog(bot))


class ContestBot(commands.Bot):
    async def setup_hook(self) -> None:
        await self.add_cog(ContestCog(self))

        if GUILD_IDS:
            for gid in GUILD_IDS:
                try:
                    guild = discord.Object(id=gid)
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                    print(f"Commands synced to server {gid}.")
                except discord.Forbidden:
                    print(f"⚠️ 403 Forbidden on server {gid}!\n   -> Ensure the bot is invited with 'applications.commands' scope.")
                except discord.HTTPException as e:
                    print(f"⚠️ HTTP error while syncing to server {gid}: {e}")

            try:
                self.tree.clear_commands(guild=None)
                await self.tree.sync()
                print("Global command cache cleared.")
            except discord.HTTPException as e:
                print(f"⚠️ Couldn't clear global commands: {e}")
        else:
            await self.tree.sync()
            print("Global commands synced.")

    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User | discord.Member) -> None:
        """Removes reactions from contest, event, and LFG messages"""
        if user.bot:
            return
        
        cog = self.get_cog("ContestCog")
        if cog and isinstance(cog, ContestCog) and cog.is_contest_or_event_message(reaction.message.id):
            try:
                await reaction.remove(user)
            except discord.HTTPException:
                pass


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            f"Missing token!\n"
            f"1. Make sure a file .env exists in: {SCRIPT_DIR}\n"
            f"2. It must contain: DISCORD_TOKEN=your_token"
        )

    intents = discord.Intents.default()
    intents.message_content = True
    bot = ContestBot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready() -> None:
        print(f"Bot is working as {bot.user} (Version 1.1) WoW, I did it!")

    bot.run(TOKEN)

