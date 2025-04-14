async def fetch_recent_trades():
    url = f"https://www43.myfantasyleague.com/{SEASON_YEAR}/export?TYPE=transactions&L={LEAGUE_ID}&TRANS_TYPE=TRADE"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                print(f"Failed to fetch trades: HTTP {resp.status}")
                return []

            xml_data = await resp.text()
            print("Fetched trade XML from MFL:")
            print(xml_data[:500])

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
                    print(f"Detected trade: {trade_id} on {timestamp}")
                    for d in details:
                        print(f"  - {d}")

            return trades
