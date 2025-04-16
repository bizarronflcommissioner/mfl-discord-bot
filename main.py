import discord
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
from datetime import datetime
import os
import re

# --- CONFIGURATION ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1359911725327056921  # Transactions
DRAFT_CHANNEL_ID = 1359911725327056922  # Drafts
LEAGUE_ID = "61663"
SEASON_YEAR = 2025
CHECK_INTERVAL = 60
DRAFT_INTERVAL = 300

intents = discord.Intents.default()
client = discord.Client(intents=intents)

franchise_names = {}
player_names = {}
posted_transactions = set()
posted_picks = set()
draft_announced = False

# --- UTILITY FUNCTIONS ---
def ordinal(n):
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")

def format_item(item):
    dp_match = re.match(r"DP_(\d+)_(\d+)", item)
    if dp_match:
        rnd, pick = dp_match.groups()
        try:
            round_num = int(rnd) + 1  # MFL uses 0-based round numbers
            pick_num = int(pick) + 1  # Already 1-based in MFL
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

# --- LOAD MFL DATA ---
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

# --- FETCH TRANSACTIONS ---
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
                    t1, t2 = tx.get("franchise"), tx.get("franchise2")
                    t1_items = [format_item(i) for i in tx.get("franchise1_gave_up", "").strip(",").split(",") if i]
                    t2_items = [format_item(i) for i in tx.get("franchise2_gave_up", "").strip(",").split(",") if i]
                    t1_line = f"{franchise_names.get(t1, t1)} traded: {', '.join(t1_items) if t1_items else '(nothing)'}"
                    t2_line = f"{franchise_names.get(t2, t2)} traded: {', '.join(t2_items) if t2_items else '(nothing)'}"
                    lines = [f"**Trade Alert ({timestamp})**", t1_line, t2_line]
                    note = tx.get("comments", "").strip()
                    offer = tx.get("message", "").strip()
                    if note: lines.append(f"Note: {note}")
                    if offer: lines.append(f"Optional Message: {offer}")
                    transactions.append("\n".join(lines))

                elif tx_type == "FREE_AGENT":
                    pid = next((p.strip() for p in raw_tx.replace("|", ",").split(",") if p.strip().isdigit()), None)
                    if pid:
                        action = "signed" if not raw_tx.startswith("|") else "released"
                        transactions.append(f"**Add/Drop Alert ({timestamp})**: {team_name} {action} {player_names.get(pid, f'Player #{pid}')}")

                elif tx_type == "AUCTION_WON":
                    parts = raw_tx.split("|")
                    if len(parts) >= 2:
                        pid, bid = parts[0], parts[1]
                        try:
                            bid_amt = float(bid) / 1_000_000
                        except:
                            bid_amt = bid
                        transactions.append(f"**Auction Win ({timestamp})**: {team_name} won {player_names.get(pid, f'Player #{pid}')} for ${bid_amt}m")

                elif tx_type == "TAXI":
                    p_up = tx.get("promoted", "").strip(",")
                    p_down = tx.get("demoted", "").strip(",")
                    moves = []
                    if p_up:
                        moves.append(f"promoted: {', '.join(player_names.get(p, f'Player #{p}') for p in p_up.split(',') if p)}")
                    if p_down:
                        moves.append(f"demoted: {', '.join(player_names.get(p, f'Player #{p}') for p in p_down.split(',') if p)}")
                    transactions.append(f"**Taxi Move ({timestamp})**: {team_name} " + " | ".join(moves))

                elif tx_type == "IR":
                    pid = next((p.strip() for p in raw_tx.replace("|", ",").split(",") if p.strip().isdigit()), None)
                    if pid:
                        transactions.append(f"**IR Move ({timestamp})**: {team_name} placed {player_names.get(pid, f'Player #{pid}')} on IR")

            return transactions

# --- FETCH DRAFT ---
async def fetch_draft():
    url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=draftResults&L={LEAGUE_ID}&JSON=1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return [], None
            data = await resp.json()
            draft_unit = data.get("draftResults", {}).get("draftUnit", [])
            if not draft_unit:
                return [], None
            return draft_unit[0].get("draftPick", []), draft_unit[0].get("startTime")

# --- LOOPS ---
async def transaction_loop():
    await client.wait_until_ready()
    channel = client.get_channel(CHANNEL_ID)
    await load_franchises()
    await load_players()
    while not client.is_closed():
        txs = await fetch_all_transactions()
        for msg in txs:
            await channel.send(msg + "\n" + "-" * 40)
        await asyncio.sleep(CHECK_INTERVAL)

async def draft_loop():
    global draft_announced
    await client.wait_until_ready()
    channel = client.get_channel(DRAFT_CHANNEL_ID)
    while not client.is_closed():
        picks, start_time = await fetch_draft()
        if not picks:
            await asyncio.sleep(DRAFT_INTERVAL)
            continue
        if not draft_announced and start_time:
            readable_time = datetime.fromtimestamp(int(start_time)).strftime('%b %d, %Y %I:%M %p')
            await channel.send(f"🏈 **The draft has started!** First pick was scheduled for {readable_time}.\n{'-' * 40}")
            draft_announced = True
        for i, pick in enumerate(picks):
            pick_id = pick["timestamp"]
            if pick_id in posted_picks:
                continue
            posted_picks.add(pick_id)
            team = franchise_names.get(pick["franchise"], f"Franchise {pick['franchise']}")
            name = pick.get("playerName", f"Player #{pick.get('player')}")
            msg = f"🏈 **Draft Pick:** {team} selected {name} (Round {pick['round']}, Pick {pick['pick']})"
            if i + 1 < len(picks):
                on_clock = picks[i + 1]["franchise"]
                msg += f"\n🕒 On the clock: {franchise_names.get(on_clock, on_clock)}"
            if i + 2 < len(picks):
                on_deck = picks[i + 2]["franchise"]
                msg += f"\n📋 On deck: {franchise_names.get(on_deck, on_deck)}"
            await channel.send(msg + "\n" + "-" * 40)
        await asyncio.sleep(DRAFT_INTERVAL)

# --- MAIN ---
@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    client.loop.create_task(transaction_loop())
    client.loop.create_task(draft_loop())

client.run(DISCORD_TOKEN)
