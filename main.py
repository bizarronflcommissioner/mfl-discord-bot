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
print(f"TOKEN: {DISCORD_TOKEN}")

CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
ROOKIE_CHANNEL_ID = 1359911725327056922  # Rookie draft channel ID
ADDDROP_CHANNEL_ID = 1359911726899921159  # Add/Drop channel ID
LEAGUE_ID = os.getenv("LEAGUE_ID")
SEASON_YEAR = 2025
CHECK_INTERVAL = 60

intents = discord.Intents.default()
client = discord.Client(intents=intents)
posted_trades = set()
posted_rookies = set()
posted_adddrops = set()

franchise_names = {}
player_names = {}

def ordinal(n):
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")

def format_item(item):
    dp_match = re.match(r"DP_(\d)_(\d+)", item)
    if dp_match:
        rnd, pick = dp_match.groups()
        return f"2025 {ordinal(int(rnd))} Round Pick (Pick {pick})"

    fp_match = re.match(r"FP_(\d{4})_(\d{4})_(\d)", item)
    if fp_match:
        team, year, rnd = fp_match.groups()
        team_name = franchise_names.get(team, f"Team {team}")
        return f"{year} {ordinal(int(rnd))} Round Pick (from {team_name})"

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

# --- DEBUG ENHANCED ADDDROP LOOP ---
async def adddrop_check_loop():
    await client.wait_until_ready()
    adddrop_channel = client.get_channel(ADDDROP_CHANNEL_ID)
    if adddrop_channel is None:
        print("❌ ERROR: Add/Drop channel not found.")
        return

    while not client.is_closed():
        print("Checking add/drops...")
        url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=transactions&L={LEAGUE_ID}&TRANS_TYPE=ADD,DROP"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    print(f"Failed to fetch add/drops: HTTP {resp.status}")
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                xml_data = await resp.text()
                root = ET.fromstring(xml_data)

                transactions_found = 0
                for tx in root.findall("transaction"):
                    tx_id = tx.get("timestamp")
                    action = tx.get("type", "").strip().upper()
                    player_id = tx.get("player")
                    team = tx.get("franchise")

                    print(f"🕵️ TX: type={action}, player_id={player_id}, team={team}, ts={tx_id}")

                    if not tx_id or not action or not player_id:
                        print("⚠️ Incomplete transaction entry. Skipping.")
                        continue

                    if tx_id in posted_adddrops:
                        continue

                    try:
                        timestamp = datetime.fromtimestamp(int(tx_id))
                    except ValueError:
                        print(f"⚠️ Invalid timestamp: {tx_id}")
                        continue

                    posted_adddrops.add(tx_id)
                    team_name = franchise_names.get(team, f"Team {team}")
                    player = player_names.get(player_id, f"Player #{player_id}")
                    msg = f"🔄 **{action} Alert ({timestamp.strftime('%b %d, %Y %I:%M %p')}):** {team_name} {action.lower()}ed {player}"
                    await adddrop_channel.send(msg)
                    transactions_found += 1

                print(f"✅ Posted {transactions_found} add/drop transactions.")

        await asyncio.sleep(CHECK_INTERVAL)

# --- rest of your bot logic continues unchanged ---
