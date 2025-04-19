import os
from dotenv import load_dotenv
import discord
import asyncio

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    await client.close()

async def run_bot():
    try:
        await client.start(DISCORD_TOKEN)
    except discord.LoginFailure:
        print("❌ Invalid token provided!")

asyncio.run(run_bot())
