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
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))  # Transactions channel
DRAFT_CHANNEL_ID = int(os.getenv("DRAFT_CHANNEL_ID"))  # Draft channel
LEAGUE_ID = os.getenv("LEAGUE_ID")
SEASON_YEAR = 2025
CHECK_INTERVAL = 60
DRAFT_CHECK_INTERVAL = 300

intents = discord.Intents.default()
client = discord.Client(intents=intents)

franchise_names = {}
player_names = {}
posted_transactions = set()
posted_picks = set()
posted_ir = set()
draft_announced = False

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

    msg = f"🎉 **Draft Pick Made!**\n{team} selected {player} (Round {round_num}, Pick {pick_num})"

    if next_pick:
        next_team = franchise_names.get(next_pick.get("franchise"), f"Franchise {next_pick.get('franchise')}")
        msg += f"\n🕒 On the clock: {next_team}"

    if on_deck_pick:
        deck_team = franchise_names.get(on_deck_pick.get("franchise"), f"Franchise {on_deck_pick.get('franchise')}")
        msg += f"\n📋 On deck: {deck_team}"

    return msg + "\n" + "-" * 40

async def load_franchises():
    url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=league&L={LEAGUE_ID}&JSON=1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            for f in data["league"]["franchises"]["franchise"]:
                franchise_names[f["id"]] = f["name"]
    print(f"✅ Loaded {len(franchise_names)} franchises.")

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
    print(f"✅ Loaded {len(player_names)} players.")

async def fetch_ir_moves(channel):
    url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=injuries&L={LEAGUE_ID}&JSON=1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                print("❌ Failed to fetch IR moves")
                return
            data = await resp.json()
            injuries = data.get("injuries", {}).get("injury", [])
            for entry in injuries:
                player_id = entry.get("player")
                status = entry.get("status")
                franchise_id = entry.get("franchise")
                if player_id and status == "IR" and player_id not in posted_ir:
                    posted_ir.add(player_id)
                    player = player_names.get(player_id, f"Player #{player_id}")
                    team = franchise_names.get(franchise_id, f"Franchise {franchise_id}")
                    await channel.send(f"🏥 **IR Move Detected**: {team} placed {player} on injured reserve\n" + "-" * 40)

async def fetch_and_post_draft_updates(channel):
    global draft_announced
    url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=draftResults&L={LEAGUE_ID}&JSON=1"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                print("Draft post error:", resp.status)
                return

            data = await resp.json()
            draft_unit = data.get("draftResults", {}).get("draftUnit", {})
            picks = draft_unit.get("draftPick", [])

            if not draft_announced and picks:
                draft_announced = True
                await channel.send(f"🏈 **The draft has begun!**\n{'-' * 40}")

            for i, pick in enumerate(picks):
                ts = pick.get("timestamp")
                if not ts or ts in posted_picks:
                    continue
                posted_picks.add(ts)
                next_pick = picks[i+1] if i+1 < len(picks) else None
                on_deck_pick = picks[i+2] if i+2 < len(picks) else None
                await channel.send(format_draft_pick_message(pick, next_pick, on_deck_pick))

async def transaction_loop():
    await client.wait_until_ready()
    tx_channel = client.get_channel(CHANNEL_ID)
    draft_channel = client.get_channel(DRAFT_CHANNEL_ID)

    if not tx_channel or not draft_channel:
        print("❌ Error: Could not find one or more channels.")
        return

    await load_franchises()
    await load_players()

    while not client.is_closed():
        print("🔁 Checking for transactions...")
        txs = await fetch_all_transactions()
        for msg in txs:
            await tx_channel.send(msg + "\n" + "-" * 40)

        print("🧾 Checking draft updates...")
        await fetch_and_post_draft_updates(draft_channel)

        print("🏥 Checking IR moves...")
        await fetch_ir_moves(tx_channel)

        await asyncio.sleep(DRAFT_CHECK_INTERVAL)

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    client.loop.create_task(transaction_loop())

client.run(DISCORD_TOKEN)
