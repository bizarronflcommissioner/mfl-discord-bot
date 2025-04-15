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
LEAGUE_ID = os.getenv("LEAGUE_ID")
SEASON_YEAR = 2025
CHECK_INTERVAL = 60

intents = discord.Intents.default()
client = discord.Client(intents=intents)
posted_transactions = set()

franchise_names = {}
player_names = {}

def ordinal(n):
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")

def format_item(item):
    dp_match = re.match(r"DP_(\d+)_(\d+)", item)
    if dp_match:
        rnd, pick = dp_match.groups()
        try:
            rnd_int = int(rnd)
            if rnd_int == 0:
                rnd_int = 1
            return f"{SEASON_YEAR} {ordinal(rnd_int)} Round Pick (Pick {pick})"
        except:
            return f"{SEASON_YEAR} Draft Pick Round {rnd}, Pick {pick}"

    fp_match = re.match(r"FP_(\d{4})_(\d{4})_(\d+)", item)
    if fp_match:
        team, year, rnd = fp_match.groups()
        try:
            rnd_int = int(rnd)
            team_name = franchise_names.get(team, f"Team {team}")
            return f"{year} {ordinal(rnd_int)} Round Pick (from {team_name})"
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
    print(f"Loaded {len(franchise_names)} franchises.")

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
    print(f"Loaded {len(player_names)} players.")

async def fetch_all_transactions():
    url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=transactions&L={LEAGUE_ID}&TRANS_TYPE=ALL"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                print(f"Failed to fetch transactions: HTTP {resp.status}")
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
                    team1 = tx.get("franchise")
                    team2 = tx.get("franchise2")
                    t1_items = [format_item(i) for i in tx.get("franchise1_gave_up", "").strip(",").split(",") if i]
                    t2_items = [format_item(i) for i in tx.get("franchise2_gave_up", "").strip(",").split(",") if i]
                    team1_line = f"{franchise_names.get(team1, team1)} traded: {', '.join(t1_items) if t1_items else '(nothing)'}"
                    team2_line = f"{franchise_names.get(team2, team2)} traded: {', '.join(t2_items) if t2_items else '(nothing)'}"
                    note = tx.get("comments", "").strip()
                    offer_msg = tx.get("message", "").strip()
                    lines = [f"**Trade Alert ({timestamp})**", team1_line, team2_line]
                    if note:
                        lines.append(f"Note: {note}")
                    if offer_msg:
                        lines.append(f"Optional Message: {offer_msg}")
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
                        player_id, bid = parts[0], parts[1]
                        try:
                            bid_amt = float(bid) / 1_000_000
                        except:
                            bid_amt = bid
                        player = player_names.get(player_id, f"Player #{player_id}")
                        transactions.append(f"**Auction Win ({timestamp})**: {team_name} won {player} for ${bid_amt}m")

                elif tx_type == "TAXI":
                    promoted = tx.get("promoted", "").strip(",")
                    demoted = tx.get("demoted", "").strip(",")
                    promo = ", ".join(player_names.get(p, f"Player #{p}") for p in promoted.split(",") if p)
                    demo = ", ".join(player_names.get(p, f"Player #{p}") for p in demoted.split(",") if p)
                    move = []
                    if promo:
                        move.append(f"promoted: {promo}")
                    if demo:
                        move.append(f"demoted: {demo}")
                    transactions.append(f"**Taxi Move ({timestamp})**: {team_name} " + " | ".join(move))

            return transactions

async def transaction_loop():
    await client.wait_until_ready()
    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        print("❌ ERROR: Cannot find channel. Check .env and permissions.")
        return

    await load_franchises()
    await load_players()

    while not client.is_closed():
        print("Checking for transactions...")
        txs = await fetch_all_transactions()
        for msg in txs:
            await channel.send(msg + "\n" + "-" * 40)
        await asyncio.sleep(CHECK_INTERVAL)

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    client.loop.create_task(transaction_loop())

client.run(DISCORD_TOKEN)
