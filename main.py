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

async def fetch_recent_trades():
    url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=transactions&L={LEAGUE_ID}&TRANS_TYPE=TRADE"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                print(f"Failed to fetch trades: HTTP {resp.status}")
                return []

            xml_data = await resp.text()
            root = ET.fromstring(xml_data)
            trades = []

            for tx in root.findall("transaction"):
                if tx.get("type") != "TRADE":
                    continue

                trade_id = tx.get("timestamp")
                timestamp = datetime.fromtimestamp(int(tx.get("timestamp")))

                note = tx.get("comments", "").strip()
                offer_message = tx.get("message", "").strip()

                team1 = tx.get("franchise")
                team2 = tx.get("franchise2")

                team1_name = franchise_names.get(team1, f"Team {team1}")
                team2_name = franchise_names.get(team2, f"Team {team2}")

                team1_items = tx.get("franchise1_gave_up", "").strip(",").split(",")
                team2_items = tx.get("franchise2_gave_up", "").strip(",").split(",")

                team1_items = [format_item(item) for item in team1_items if item]
                team2_items = [format_item(item) for item in team2_items if item]

                details = []
                if team1_items:
                    details.append(f"{team1_name} traded: {', '.join(team1_items)}")
                if team2_items:
                    details.append(f"{team2_name} traded: {', '.join(team2_items)}")
                if note:
                    details.append(f"📝 Note: {note}")
                if offer_message:
                    details.append(f"📬 Optional Message to Include With Trade Offer Email:\n> {offer_message}")

                if details:
                    trades.append((trade_id, timestamp, details))

            return trades

async def fetch_rookie_draft_picks():
    url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=draftResults&L={LEAGUE_ID}&JSON=1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            return data.get("draftResults", {}).get("draftUnit", [{}])[0].get("draftPick", [])

async def rookie_post_check_loop():
    await client.wait_until_ready()
    rookie_channel = client.get_channel(ROOKIE_CHANNEL_ID)
    if rookie_channel is None:
        print("❌ ERROR: Rookie channel not found.")
        return

    while not client.is_closed():
        print("Checking rookie draft picks...")
        draft_picks = await fetch_rookie_draft_picks()

        for pick in draft_picks:
            pick_id = pick["timestamp"]
            if pick_id in posted_rookies:
                continue

            posted_rookies.add(pick_id)
            franchise = franchise_names.get(pick["franchise"], f"Franchise {pick['franchise']}")
            player = player_names.get(pick["player"], f"Player #{pick['player']}")
            round_num = pick.get("round")
            pick_num = pick.get("pick")

            msg = f"🏆 **Rookie Draft Pick:** {franchise} selected {player} (Round {round_num}, Pick {pick_num})"
            await rookie_channel.send(msg)

        await asyncio.sleep(CHECK_INTERVAL)

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

                for tx in root.findall("transaction"):
                    tx_id = tx.get("timestamp")
                    if tx_id in posted_adddrops:
                        continue

                    posted_adddrops.add(tx_id)
                    timestamp = datetime.fromtimestamp(int(tx_id))
                    team = tx.get("franchise")
                    team_name = franchise_names.get(team, f"Team {team}")
                    player_id = tx.get("player")
                    action = tx.get("type")
                    player = player_names.get(player_id, f"Player #{player_id}")
                    msg = f"🔄 **{action} Alert ({timestamp.strftime('%b %d, %Y %I:%M %p')}):** {team_name} {action.lower()}ed {player}"
                    await adddrop_channel.send(msg)

        await asyncio.sleep(CHECK_INTERVAL)

async def trade_check_loop():
    await client.wait_until_ready()
    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        print("❌ ERROR: Cannot find main trade channel.")
        return

    await load_franchises()
    await load_players()

    while not client.is_closed():
        print("Checking for trades...")
        trades = await fetch_recent_trades()

        for trade_id, timestamp, details in trades:
            if trade_id not in posted_trades:
                posted_trades.add(trade_id)
                trade_msg = f"📦 **Trade Alert ({timestamp.strftime('%b %d, %Y')}):**\n" + "\n".join(details)
                await channel.send(trade_msg)

        await asyncio.sleep(CHECK_INTERVAL)

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    client.loop.create_task(trade_check_loop())
    client.loop.create_task(rookie_post_check_loop())
    client.loop.create_task(adddrop_check_loop())

client.run(DISCORD_TOKEN)
