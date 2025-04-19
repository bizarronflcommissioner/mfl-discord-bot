import discord
from discord.ext import commands
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
from datetime import datetime
from dotenv import load_dotenv
import os
import re
import json

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))  # Transactions channel
DRAFT_CHANNEL_ID = int(os.getenv("DRAFT_CHANNEL_ID"))  # Draft channel
LEAGUE_ID = os.getenv("LEAGUE_ID")
SEASON_YEAR = 2025
DRAFT_CHECK_INTERVAL = 300
TRANSACTION_CHECK_INTERVAL = 300

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

franchise_names = {}
player_names = {}
posted_transactions = set()
posted_picks = set()
posted_ir = set()
draft_announced = False

with open("user_map.json", "r") as f:
    user_map = json.load(f)

def ordinal(n):
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")

def format_item(item):
    dp_match = re.match(r"DP_(\d+)_(\d+)", item)
    if dp_match:
        rnd, pick = dp_match.groups()
        try:
            round_num = int(rnd) + 1
            pick_num = int(pick) + 1
            return f"{SEASON_YEAR} {ordinal(round_num)} Round Pick (Pick {pick_num})"
        except:
            return f"{SEASON_YEAR} Draft Pick Round {rnd}, Pick {pick}"

    fp_match = re.match(r"FP_(\d{4})_(\d{4})_(\d+)", item)
    if fp_match:
        team, year, rnd = fp_match.groups()
        try:
            round_num = int(rnd)
            team_name = franchise_names.get(team, f"Team {team}")
            return f"{year} {ordinal(round_num)} Round Pick (from {team_name})"
        except:
            return f"{year} Draft Pick Round {rnd} (from {team_name})"

    if item.isdigit():
        return player_names.get(item, f"Player #{item}")

    return item

def format_draft_pick_message(pick, next_pick=None, on_deck_pick=None):
    franchise_id = pick.get("franchise")
    player_id = pick.get("player")
    round_num = pick.get("round")
    pick_num = pick.get("pick")

    team = franchise_names.get(franchise_id, f"Franchise {franchise_id}")
    player = player_names.get(player_id, f"Player #{player_id}")

    msg = f"\U0001F389 **Draft Pick Made!**\n{team} selected {player} (Round {round_num}, Pick {pick_num})"

    if next_pick:
        next_id = next_pick.get("franchise")
        next_mention = f"<@{user_map.get(next_id)}>" if next_id in user_map else franchise_names.get(next_id, f"Franchise {next_id}")
        msg += f"\n\U0001F552 On the clock: {next_mention}"

    if on_deck_pick:
        deck_id = on_deck_pick.get("franchise")
        deck_team = franchise_names.get(deck_id, f"Franchise {deck_id}")
        msg += f"\n\U0001F4CB On deck: {deck_team}"

    return msg + "\n" + "-" * 40

async def load_franchises():
    url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=league&L={LEAGUE_ID}&JSON=1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            for f in data["league"]["franchises"]["franchise"]:
                franchise_names[f["id"]] = f["name"]

async def load_players():
    url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=players&L={LEAGUE_ID}&JSON=1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            for player in data["players"]["player"]:
                pid = player.get("id")
                name = player.get("name", f"Player #{pid}")
                if pid:
                    player_names[pid] = name

async def fetch_and_post_draft_updates(channel):
    global draft_announced
    url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=draftResults&L={LEAGUE_ID}&JSON=1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                print(f"❌ Draft fetch failed: {resp.status}")
                return

            data = await resp.json()
            draft_unit = data.get("draftResults", {}).get("draftUnit", {})
            picks = draft_unit.get("draftPick", [])

            if not picks:
                return

            if not draft_announced:
                draft_announced = True
                await channel.send("🏈 **The draft has begun!**\n" + "-" * 40)

            for i, pick in enumerate(picks):
                ts = pick.get("timestamp")
                if not ts or ts in posted_picks:
                    continue

                posted_picks.add(ts)
                next_pick = picks[i+1] if i+1 < len(picks) else None
                on_deck_pick = picks[i+2] if i+2 < len(picks) else None

                msg = format_draft_pick_message(pick, next_pick, on_deck_pick)
                await channel.send(msg)

async def fetch_and_post_transactions(channel):
    print("🧾 Checking for new transactions...")
    url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=transactions&L={LEAGUE_ID}&TRANS_TYPE=ALL"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            xml_data = await resp.text()
            root = ET.fromstring(xml_data)
            for tx in root.findall("transaction"):
                tx_id = tx.get("timestamp")
                if tx_id in posted_transactions:
                    continue
                posted_transactions.add(tx_id)
                tx_type = tx.get("type")
                team = tx.get("franchise")
                timestamp = datetime.fromtimestamp(int(tx_id)).strftime('%b %d, %Y %I:%M %p')
                team_name = franchise_names.get(team, f"Team {team}")

                if tx_type == "FREE_AGENT":
                    raw_tx = tx.get("transaction", "")
                    player_id = next((p.strip() for p in raw_tx.replace("|", ",").split(",") if p.strip().isdigit()), None)
                    if player_id:
                        is_add = not raw_tx.startswith("|")
                        action = "signed" if is_add else "released"
                        emoji = "🟢" if is_add else "🔴"
                        player = player_names.get(player_id, f"Player #{player_id}")
                        await channel.send(f"{emoji} **Add/Drop Alert ({timestamp})**: {team_name} {action} {player}\n" + "-" * 40)

                elif tx_type == "TRADE":
                    team2 = tx.get("franchise2")
                    t1_items = [format_item(i) for i in tx.get("franchise1_gave_up", "").strip(",").split(",") if i]
                    t2_items = [format_item(i) for i in tx.get("franchise2_gave_up", "").strip(",").split(",") if i]
                    note = tx.get("comments", "").strip()
                    offer_msg = tx.get("message", "").strip()
                    lines = [f"🔄 **Trade Alert ({timestamp})**",
                             f"{franchise_names.get(team, team)} traded: {', '.join(t1_items)}",
                             f"{franchise_names.get(team2, team2)} traded: {', '.join(t2_items)}"]
                    if note:
                        lines.append(f"Note: {note}")
                    if offer_msg:
                        lines.append(f"Optional Message: {offer_msg}")
                    await channel.send("\n".join(lines) + "\n" + "-" * 40)

async def transaction_loop():
    await bot.wait_until_ready()
    draft_channel = bot.get_channel(DRAFT_CHANNEL_ID)
    tx_channel = bot.get_channel(CHANNEL_ID)
    if not draft_channel or not tx_channel:
        print("❌ Channel not found.")
        return
    await load_franchises()
    await load_players()
    while not bot.is_closed():
        print("🔁 Running draft update loop...")
        await fetch_and_post_draft_updates(draft_channel)
        await fetch_and_post_transactions(tx_channel)
        await asyncio.sleep(DRAFT_CHECK_INTERVAL)

@bot.command(name="setuser")
async def setuser(ctx, franchise_id: str, user: discord.Member):
    franchise_id = franchise_id.zfill(4)
    if franchise_id not in franchise_names:
        await ctx.send(f"❌ Franchise ID `{franchise_id}` not found.")
        return
    user_map[franchise_id] = str(user.id)
    try:
        with open("user_map.json", "w") as f:
            json.dump(user_map, f, indent=2)
        await ctx.send(f"✅ Linked {franchise_names[franchise_id]} ({franchise_id}) to {user.mention}")
    except Exception as e:
        await ctx.send(f"❌ Failed to save user map: {e}")

@bot.command(name="listusers")
async def listusers(ctx):
    chunks = list(franchise_names.items())
    for i in range(0, len(chunks), 25):
        embed = discord.Embed(title="📋 MFL Franchise → Discord User Links", color=discord.Color.blue())
        for fid, name in chunks[i:i+25]:
            discord_id = user_map.get(fid)
            mention = f"<@{discord_id}>" if discord_id else "❌ Not Set"
            embed.add_field(name=f"{name} ({fid})", value=mention, inline=False)
        await ctx.send(embed=embed)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    bot.loop.create_task(transaction_loop())

bot.run(DISCORD_TOKEN)
