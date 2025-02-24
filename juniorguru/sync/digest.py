import textwrap
from datetime import date, timedelta

import click
from discord import Color, Embed

from juniorguru.cli.sync import main as cli
from juniorguru.lib import discord_sync, loggers
from juniorguru.lib.discord_club import (
    CLUB_GUILD,
    ClubChannelID,
    ClubClient,
    is_message_older_than,
)
from juniorguru.lib.md import md_as_text, neutralize_urls
from juniorguru.lib.mutations import mutating_discord
from juniorguru.lib.reading_time import reading_time
from juniorguru.models.base import db
from juniorguru.models.club import ClubMessage


logger = loggers.from_path(__file__)


DIGEST_EMOJI = "📰"

TOP_MESSAGES_LIMIT = 5

TOP_CHANNELS_LIMIT = 5


@cli.sync_command(dependencies=["club-content"])
@click.option("--force-since", default=None, type=click.DateTime(["%Y-%m-%d"]))
def main(force_since):
    discord_sync.run(discord_task, force_since.date() if force_since else None)


@db.connection_context()
async def discord_task(client: ClubClient, force_since: date):
    since = force_since or date.today() - timedelta(weeks=1)
    message = ClubMessage.last_bot_message(ClubChannelID.ANNOUNCEMENTS, DIGEST_EMOJI)

    if not is_message_older_than(message, since):
        if not force_since:
            logger.info("Digest not needed")
            return
        logger.warning("Digest forced!")

    if not force_since and message:
        since = message.created_at.date()
    logger.info(f"Analyzing since {since}")

    content = f"{DIGEST_EMOJI} Co se tu dělo za poslední týden? (od {since:%-d.%-m.})"

    logger.info(f"Listing {TOP_MESSAGES_LIMIT} top messages")
    messages = ClubMessage.digest_listing(since, limit=TOP_MESSAGES_LIMIT)
    for n, message in enumerate(messages, start=1):
        logger.info(
            f"Message #{n}: {message.upvotes_count} votes for {message.author.display_name} in #{message.channel_name}, {message.url}"
        )
    messages_desc = (
        "Pokud je něco zajímavé, nebo ti to pomohlo, reaguj palecem 👍, srdíčkem ❤️, apod. "
        "Oceníš autory a pomůžeš tomu, aby se příspěvek objevil i tady.\n\n"
    )
    messages_desc += "\n\n".join(format_message(message) for message in messages)
    messages_embed = Embed(
        title=f"{TOP_MESSAGES_LIMIT} nej příspěvků",
        color=Color.light_grey(),
        description=messages_desc,
    )

    logger.info(f"Listing {TOP_CHANNELS_LIMIT} top channels")
    channels_digest = ClubMessage.digest_channels(since, limit=TOP_CHANNELS_LIMIT)
    for n, channel_digest in enumerate(channels_digest, start=1):
        logger.info(
            f"Channel #{n}: {channel_digest['size']} characters in {channel_digest['channel_name']!r}, parent channel #{channel_digest['parent_channel_name']}"
        )
    channels_desc = "\n\n".join(
        format_channel_digest(channel_digest) for channel_digest in channels_digest
    )
    channels_embed = Embed(
        title="Kde se hodně diskutovalo",
        color=Color.from_rgb(70, 154, 233),
        description=channels_desc,
    )

    channel = await client.fetch_channel(ClubChannelID.ANNOUNCEMENTS)
    with mutating_discord(channel) as proxy:
        await proxy.send(content, embeds=[messages_embed, channels_embed])


def format_message(message: ClubMessage) -> str:
    return (
        f"{message.upvotes_count}× láska pro **{message.author.display_name}** v {format_channel(message)}\n"
        f"{format_content(message.content)}\n"
        f"[Číst příspěvek]({message.url})"
    )


def format_channel_digest(channel_digest: dict) -> str:
    text = ""
    if channel_digest["channel_id"] == channel_digest["parent_channel_id"]:
        text += f'**#{channel_digest["channel_name"]}**'
    else:
        text += f'**{channel_digest["channel_name"]}** v #{channel_digest["parent_channel_name"]}'
    text += (
        "\n"
        f'{reading_time(channel_digest["size"])} min čtení'
        " – "
        f'[Číst diskuzi](https://discord.com/channels/{CLUB_GUILD}/{channel_digest["channel_id"]}/)'
    )
    return text


def format_content(content: str) -> str:
    text = md_as_text(neutralize_urls((content)), newline=" ")
    text_short = textwrap.shorten(text, 150, placeholder="…")
    return f"> {text_short}"


def format_channel(message: ClubMessage) -> str:
    text = f"#{message.parent_channel_name}"
    if message.channel_id != message.parent_channel_id:
        text += f", vlákno „{message.channel_name}”"
    return text
