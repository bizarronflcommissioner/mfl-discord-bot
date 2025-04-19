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
draft_announced = False
notified_users = set()

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

async def fetch_and_post_draft_updates():
    draft_channel = bot.get_channel(DRAFT_CHANNEL_ID)
    if not draft_channel:
        print("❌ Could not find the draft channel.")
        return

    while not bot.is_closed():
        url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=draftResults&L={LEAGUE_ID}&JSON=1"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                draft_unit = data.get("draftResults", {}).get("draftUnit", {})
                picks = draft_unit.get("draftPick", [])

                global draft_announced
                if not draft_announced and picks:
                    draft_announced = True
                    await draft_channel.send(f"\U0001F3C8 **The draft has begun!**\n{'-' * 40}")

                for i, pick in enumerate(picks):
                    ts = pick.get("timestamp")
                    if not ts or ts in posted_picks:
                        continue
                    posted_picks.add(ts)
                    next_pick = picks[i+1] if i+1 < len(picks) else None
                    on_deck_pick = picks[i+2] if i+2 < len(picks) else None
                    await draft_channel.send(format_draft_pick_message(pick, next_pick, on_deck_pick))

                    if next_pick:
                        next_id = next_pick.get("franchise")
                        if next_id in user_map and next_id not in notified_users:
                            user = await bot.fetch_user(int(user_map[next_id]))
                            if user:
                                await user.send(f"⏰ You're on the clock in the draft for {franchise_names.get(next_id)}!")
                                notified_users.add(next_id)

        await asyncio.sleep(DRAFT_CHECK_INTERVAL)

async def fetch_and_post_transactions():
    txn_channel = bot.get_channel(CHANNEL_ID)
    if not txn_channel:
        print("❌ Could not find the transactions channel.")
        return

    while not bot.is_closed():
        url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=transactions&L={LEAGUE_ID}&JSON=1"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                txns = data.get("transactions", {}).get("transaction", [])
                if isinstance(txns, dict):
                    txns = [txns]

                for tx in txns:
                    tx_id = tx.get("timestamp")
                    if not tx_id or tx_id in posted_transactions:
                        continue
                    posted_transactions.add(tx_id)

                    t_type = tx.get("type", "").lower()
                    f_id = tx.get("franchise", "0000")
                    team = franchise_names.get(f_id, f"Franchise {f_id}")
                    timestamp = datetime.fromtimestamp(int(tx_id)).strftime("%b %d, %Y %I:%M %p")

                    try:
                        if t_type == "free_agent":
                            transaction_data = tx.get("transaction", "")
                            adds = re.findall(r"\|(\d+)", transaction_data)
                            drops = re.findall(r"(\d+),\|", transaction_data)

                            for pid in adds:
                                player = player_names.get(pid, f"Player #{pid}")
                                msg = f"🟢 Add/Drop Alert ({timestamp}): {team} signed {player}\n{'-' * 40}"
                                await txn_channel.send(msg)

                            for pid in drops:
                                player = player_names.get(pid, f"Player #{pid}")
                                msg = f"🔴 Add/Drop Alert ({timestamp}): {team} released {player}\n{'-' * 40}"
                                await txn_channel.send(msg)

                        elif t_type == "add":
                            player = player_names.get(tx.get("player"), f"Player #{tx.get('player')}")
                            msg = f"🟢 Add/Drop Alert ({timestamp}): {team} signed {player}\n{'-' * 40}"
                            await txn_channel.send(msg)

                        elif t_type == "drop":
                            player = player_names.get(tx.get("player"), f"Player #{tx.get('player')}")
                            msg = f"🔴 Add/Drop Alert ({timestamp}): {team} released {player}\n{'-' * 40}"
                            await txn_channel.send(msg)

                        elif t_type == "ir":
                            act = tx.get("activated", "").strip(",")
                            deact = tx.get("deactivated", "").strip(",")
                            if act:
                                player = player_names.get(act, f"Player #{act}")
                                msg = f"🏥 IR Alert ({timestamp}): {team} activated {player} from IR\n{'-' * 40}"
                            elif deact:
                                player = player_names.get(deact, f"Player #{deact}")
                                msg = f"🏥 IR Alert ({timestamp}): {team} moved {player} to IR\n{'-' * 40}"
                            else:
                                msg = f"🏥 IR Alert ({timestamp}): {team} made an IR move\n{'-' * 40}"
                            await txn_channel.send(msg)

                        elif t_type == "taxi":
                            promoted = tx.get("promoted", "").strip(",").split(",")
                            demoted = tx.get("demoted", "").strip(",").split(",")

                            for pid in promoted:
                                if pid:
                                    player = player_names.get(pid, f"Player #{pid}")
                                    msg = f"🚕 Taxi Alert ({timestamp}): {team} promoted {player} from taxi\n{'-' * 40}"
                                    await txn_channel.send(msg)

                            for pid in demoted:
                                if pid:
                                    player = player_names.get(pid, f"Player #{pid}")
                                    msg = f"🚕 Taxi Alert ({timestamp}): {team} demoted {player} to taxi\n{'-' * 40}"
                                    await txn_channel.send(msg)

                        elif t_type == "trade":
                            sent = tx.get("franchise1_gave_up", "").split(",")
                            received = tx.get("franchise2_gave_up", "").split(",")
                            other = tx.get("franchise2")
                            other_team = franchise_names.get(other, f"Franchise {other}")
                            note = tx.get("comments", "")

                            msg1 = f"🔄 Trade Alert ({timestamp})\n{team} traded: {', '.join(format_item(i) for i in sent if i)}\n{other_team}  traded: {', '.join(format_item(i) for i in received if i)}"
                            await txn_channel.send(msg1 + "\n" + "-" * 40)

                            if note:
                                msg2 = f"🔄 Trade Alert ({timestamp})\n{team} traded: {', '.join(format_item(i) for i in received if i)}\n{other_team}  traded: {', '.join(format_item(i) for i in sent if i)}\nNote: {note}"
                                await txn_channel.send(msg2 + "\n" + "-" * 40)

                    except Exception as e:
                        print(f"❌ Error processing transaction: {tx} | {e}")

        await asyncio.sleep(TRANSACTION_CHECK_INTERVAL)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    await load_franchises()
    await load_players()

    bot.loop.create_task(fetch_and_post_draft_updates())
    bot.loop.create_task(fetch_and_post_transactions())

bot.run(DISCORD_TOKEN)
