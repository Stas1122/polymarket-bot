import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Any
import aiohttp

logger = logging.getLogger(__name__)

DATA_FILE = "traders.json"

POLYMARKET_CLOB_API = "https://clob.polymarket.com"
POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com"


class PolymarketMonitor:
    def __init__(self):
        self.traders: Dict[str, List[Dict]] = {}  # {chat_id: [{address, last_trade_id}]}
        self._load()

    def _load(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r") as f:
                    self.traders = json.load(f)
                logger.info(f"Loaded {self.get_total_traders()} traders from storage")
            except Exception as e:
                logger.error(f"Failed to load traders: {e}")
                self.traders = {}

    def _save(self):
        try:
            with open(DATA_FILE, "w") as f:
                json.dump(self.traders, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save traders: {e}")

    def add_trader(self, chat_id: str, address: str) -> str:
        address = address.lower()
        if chat_id not in self.traders:
            self.traders[chat_id] = []

        for t in self.traders[chat_id]:
            if t["address"] == address:
                return "exists"

        self.traders[chat_id].append({
            "address": address,
            "last_trade_timestamp": None,
            "last_trade_ids": []
        })
        self._save()
        return "added"

    def remove_trader(self, chat_id: str, address: str):
        address = address.lower()
        if chat_id in self.traders:
            self.traders[chat_id] = [
                t for t in self.traders[chat_id] if t["address"] != address
            ]
            self._save()

    def get_traders(self, chat_id: str) -> List[Dict]:
        return self.traders.get(chat_id, [])

    def get_total_traders(self) -> int:
        return sum(len(v) for v in self.traders.values())

    def _update_last_trade(self, chat_id: str, address: str, trade_ids: List[str], timestamp: str):
        if chat_id in self.traders:
            for t in self.traders[chat_id]:
                if t["address"] == address:
                    t["last_trade_ids"] = trade_ids
                    t["last_trade_timestamp"] = timestamp
        self._save()

    async def fetch_recent_trades(self, address: str, session: aiohttp.ClientSession) -> List[Dict]:
        """Fetch recent trades for a trader from Polymarket CLOB API."""
        try:
            url = f"{POLYMARKET_CLOB_API}/trades"
            params = {
                "maker_address": address,
                "limit": 20
            }
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    trades = data.get("data", [])
                    return trades
                else:
                    logger.warning(f"CLOB API returned {resp.status} for {address}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching trades for {address}: {e}")
            return []

    async def fetch_market_info(self, condition_id: str, session: aiohttp.ClientSession) -> Dict:
        """Fetch market info from Gamma API."""
        try:
            url = f"{POLYMARKET_GAMMA_API}/markets"
            params = {"condition_ids": condition_id}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and len(data) > 0:
                        return data[0]
        except Exception as e:
            logger.debug(f"Error fetching market info: {e}")
        return {}

    def _parse_trade(self, raw: Dict, market_info: Dict, trader_address: str) -> Dict:
        """Parse raw trade data into notification format."""
        # Determine side from trader perspective
        maker_addr = raw.get("maker_address", "").lower()
        taker_addr = raw.get("taker_address", "").lower()

        # CLOB trade fields
        price = float(raw.get("price", 0))
        size = float(raw.get("size", 0))
        usd_value = price * size

        # Side: if trader is maker, use maker_side; else opposite
        raw_side = raw.get("side", "BUY")
        if taker_addr == trader_address and raw_side.upper() == "BUY":
            side = "SELL"
        elif taker_addr == trader_address and raw_side.upper() == "SELL":
            side = "BUY"
        else:
            side = raw_side

        # Outcome from token_id matching
        outcome = raw.get("outcome", "Yes")

        # Market info
        market_slug = market_info.get("slug", "")
        market_title = market_info.get("question", market_info.get("title", ""))
        market_url = f"https://polymarket.com/event/{market_slug}" if market_slug else ""

        # Timestamp
        ts_raw = raw.get("match_time", raw.get("created_at", ""))
        try:
            dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            timestamp = dt.strftime("%d.%m.%Y %H:%M UTC")
        except Exception:
            timestamp = ts_raw[:19] if ts_raw else "—"

        return {
            "id": raw.get("id", raw.get("trade_id", "")),
            "trader_address": trader_address,
            "side": side,
            "outcome": outcome,
            "price": price,
            "size": size,
            "usd_value": usd_value,
            "market_title": market_title,
            "market_url": market_url,
            "timestamp": timestamp,
            "condition_id": raw.get("condition_id", ""),
        }

    async def check_new_trades(self) -> List[Tuple[str, Dict]]:
        """Check all monitored traders for new trades. Returns list of (chat_id, trade)."""
        if not self.traders:
            return []

        notifications = []

        async with aiohttp.ClientSession() as session:
            for chat_id, trader_list in self.traders.items():
                for trader in trader_list:
                    address = trader["address"]
                    known_ids = set(trader.get("last_trade_ids", []))

                    trades = await self.fetch_recent_trades(address, session)
                    if not trades:
                        continue

                    # Find new trades (not seen before)
                    new_trades = []
                    current_ids = []

                    for t in trades:
                        tid = t.get("id", t.get("trade_id", ""))
                        current_ids.append(tid)

                        # First run — just record existing trades, don't notify
                        if not known_ids:
                            continue

                        if tid not in known_ids:
                            new_trades.append(t)

                    # Update known IDs
                    if current_ids:
                        self._update_last_trade(
                            chat_id, address,
                            current_ids[:10],  # keep last 10 IDs
                            trades[0].get("match_time", "")
                        )

                    # Fetch market info and build notifications
                    for raw_trade in new_trades:
                        condition_id = raw_trade.get("condition_id", "")
                        market_info = {}
                        if condition_id:
                            market_info = await self.fetch_market_info(condition_id, session)

                        parsed = self._parse_trade(raw_trade, market_info, address)
                        notifications.append((chat_id, parsed))
                        logger.info(f"New trade detected for {address}: {parsed['id']}")

                    # Small delay between traders
                    await asyncio.sleep(0.5)

        return notifications
