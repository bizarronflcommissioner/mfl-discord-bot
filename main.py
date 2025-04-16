import discord
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
from datetime import datetime
from dotenv import load_dotenv
import os
import re

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
DRAFT_CHANNEL_ID = int(os.getenv("DRAFT_CHANNEL_ID"))
LEAGUE_ID = os.getenv("LEAGUE_ID")
SEASON_YEAR = 2025
CHECK_INTERVAL = 60
DRAFT_CHECK_INTERVAL = 300

intents = discord.Intents.default()
client = discord.Client(intents=intents)

posted_transactions = set()
posted_draft_picks = set()
draft_started = False

franchise_names = {}
player_names = {}

def ordinal(n):
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")

def format_item(item):
    dp_match = re.match(r"DP_(\d+)_(\d+)", item)
    if dp_match:
        rnd, pick = dp_match.groups()
        try:
            round_num = int(rnd) + 1
            pick_num = int(pick) + 1  # Fix for 1-based pick numbers
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

async def fetch_all_transactions():
    url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=transactions&L={LEAGUE_ID}&TRANS_TYPE=ALL"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return []

            xml_data = await resp.text()
            root = ET.fromstring(xml_data)
            transactions = []

            for tx in root.findall("transaction"):
                tx_id = tx.get("timestamp")
                if tx_id in posted_transactions:
                    continue
                posted_transactions.add(tx_id)

                timestamp = datetime.fromtimestamp(int(tx_id)).strftime('%b %d, %Y %I:%M %p')
                tx_type = tx.get("type")
                team = tx.get("franchise")
                team_name = franchise_names.get(team, f"Team {team}")
                raw_tx = tx.get("transaction", "")

                if tx_type == "TRADE":
                    t1 = tx.get("franchise")
                    t2 = tx.get("franchise2")
                    t1_items = [format_item(i) for i in tx.get("franchise1_gave_up", "").strip(",").split(",") if i]
                    t2_items = [format_item(i) for i in tx.get("franchise2_gave_up", "").strip(",").split(",") if i]
                    lines = [f"**Trade Alert ({timestamp})**"]
                    lines.append(f"{franchise_names.get(t1)} traded: {', '.join(t1_items) or '(nothing)'}")
                    lines.append(f"{franchise_names.get(t2)} traded: {', '.join(t2_items) or '(nothing)'}")
                    if tx.get("comments"):
                        lines.append(f"Note: {tx.get('comments')}")
                    if tx.get("message"):
                        lines.append(f"Optional Message: {tx.get('message')}")
                    transactions.append("\n".join(lines))

                elif tx_type == "FREE_AGENT":
                    player_id = next((p.strip() for p in raw_tx.replace("|", ",").split(",") if p.strip().isdigit()), None)
                    if player_id:
                        action = "signed" if not raw_tx.startswith("|") else "released"
                        player = player_names.get(player_id, f"Player #{player_id}")
                        transactions.append(f"**Add/Drop Alert ({timestamp})**: {team_name} {action} {player}")

                elif tx_type == "AUCTION_WON":
                    parts = raw_tx.split("|")
                    if len(parts) >= 2:
                        pid, bid = parts[0], parts[1]
                        bid_amt = float(bid) / 1_000_000
                        player = player_names.get(pid, f"Player #{pid}")
                        transactions.append(f"**Auction Win ({timestamp})**: {team_name} won {player} for ${bid_amt}m")

                elif tx_type == "TAXI":
                    promo = [player_names.get(p, f"Player #{p}") for p in tx.get("promoted", "").strip(",").split(",") if p]
                    demo = [player_names.get(p, f"Player #{p}") for p in tx.get("demoted", "").strip(",").split(",") if p]
                    msg = f"**Taxi Move ({timestamp})**: {team_name} "
                    if promo: msg += f"promoted: {', '.join(promo)} "
                    if demo: msg += f"demoted: {', '.join(demo)}"
                    transactions.append(msg)

                elif tx_type == "IR":
                    player_id = next((p.strip() for p in raw_tx.replace("|", ",").split(",") if p.strip().isdigit()), None)
                    if player_id:
                        player = player_names.get(player_id, f"Player #{player_id}")
                        transactions.append(f"**IR Move ({timestamp})**: {team_name} placed {player} on IR")

            return transactions

async def fetch_draft_results():
    url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=draftResults&L={LEAGUE_ID}&JSON=1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.json()

async def post_draft_updates():
    global draft_started
    await client.wait_until_ready()
    channel = client.get_channel(DRAFT_CHANNEL_ID)
    if channel is None:
        print("❌ Draft channel not found.")
        return

    while not client.is_closed():
        try:
            data = await fetch_draft_results()
            draft_unit = data.get("draftResults", {}).get("draftUnit", [{}])[0]
            picks = draft_unit.get("draftPick", [])
            start_time = draft_unit.get("startTime")

            if not draft_started and start_time:
                readable_start = datetime.fromtimestamp(int(start_time)).strftime('%b %d, %Y %I:%M %p')
                await channel.send(f"🏈 **The draft has started!** First pick was scheduled for {readable_start}.")
                draft_started = True

            for i, pick in enumerate(picks):
                pick_id = pick["timestamp"]
                if pick_id in posted_draft_picks:
                    continue
                posted_draft_picks.add(pick_id)

                franchise_id = pick.get("franchise")
                player_name = pick.get("playerName", f"Player #{pick.get('player')}")
                round_num = int(pick.get("round"))
                pick_num = int(pick.get("pick")) + 1
                original_owner = franchise_names.get(pick.get("originalPickFor"), f"Franchise {pick.get('originalPickFor')}")
                franchise_name = franchise_names.get(franchise_id, f"Franchise {franchise_id}")

                msg = (
                    f"🎉 **Draft Pick Made!**\n"
                    f"{franchise_name} selected {player_name} (Round {round_num}, Pick {pick_num})\n"
                    f"Pick owned by: {original_owner}"
                )
                await channel.send(msg)

        except Exception as e:
            import traceback
            print("Draft post error:")
            traceback.print_exc()


        await asyncio.sleep(DRAFT_CHECK_INTERVAL)

async def transaction_loop():
    await client.wait_until_ready()
    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        print("❌ Transaction channel not found.")
        return

    await load_franchises()
    await load_players()

    while not client.is_closed():
        print("🔁 Checking for transactions...")
        txs = await fetch_all_transactions()
        for msg in txs:
            await channel.send(msg + "\n" + "-" * 40)
        await asyncio.sleep(CHECK_INTERVAL)

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    client.loop.create_task(transaction_loop())
    client.loop.create_task(post_draft_updates())

client.run(DISCORD_TOKEN)
