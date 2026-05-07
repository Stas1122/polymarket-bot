import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Tuple, Dict
import aiohttp

logger = logging.getLogger(__name__)

DATA_FILE = "traders.json"

POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com"
POLYMARKET_DATA_API = "https://data-api.polymarket.com"


class PolymarketMonitor:
    def __init__(self):
        self.traders: Dict[str, List[Dict]] = {}
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
            "last_trade_ids": [],
            "positions": {}
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

    def _update_trader(self, chat_id: str, address: str, trade_ids: List[str], positions: dict):
        if chat_id in self.traders:
            for t in self.traders[chat_id]:
                if t["address"] == address:
                    t["last_trade_ids"] = trade_ids
                    t["positions"] = positions
        self._save()

    def _get_trader_data(self, chat_id: str, address: str):
        if chat_id in self.traders:
            for t in self.traders[chat_id]:
                if t["address"] == address:
                    return t.get("last_trade_ids", []), t.get("positions", {})
        return [], {}

    async def fetch_trades(self, address: str, session: aiohttp.ClientSession) -> List[Dict]:
        """Fetch trades via data-api (no auth required)."""
        try:
            url = f"{POLYMARKET_DATA_API}/activity"
            params = {
                "user": address,
                "limit": 20,
                "offset": 0
            }
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        return data
                    return data.get("data", [])
                else:
                    logger.warning(f"data-api activity returned {resp.status} for {address}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching trades for {address}: {e}")
            return []

    async def fetch_positions(self, address: str, session: aiohttp.ClientSession) -> List[Dict]:
        """Fetch open positions via data-api."""
        try:
            url = f"{POLYMARKET_DATA_API}/positions"
            params = {"user": address, "sizeThreshold": "0.01", "limit": 50}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        return data
                    return data.get("data", [])
                else:
                    logger.warning(f"data-api positions returned {resp.status} for {address}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching positions for {address}: {e}")
            return []

    def _parse_activity(self, raw: Dict, trader_address: str) -> Dict:
        """Parse activity item from data-api."""
        trade_type = raw.get("type", "").upper()  # BUY / SELL / REDEEM etc.
        outcome = raw.get("outcome", raw.get("side", ""))
        price = float(raw.get("price", 0))
        size = float(raw.get("size", raw.get("shares", 0)))
        usd_value = float(raw.get("usdcSize", raw.get("amount", price * size)))

        market_title = raw.get("title", raw.get("market", {}).get("question", ""))
        market_slug = raw.get("slug", raw.get("market", {}).get("slug", ""))
        market_url = f"https://polymarket.com/event/{market_slug}" if market_slug else ""

        ts_raw = raw.get("timestamp", raw.get("createdAt", raw.get("created_at", "")))
        try:
            if isinstance(ts_raw, (int, float)):
                dt = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
            else:
                dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            timestamp = dt.strftime("%d.%m.%Y %H:%M UTC")
        except Exception:
            timestamp = str(ts_raw)[:19] if ts_raw else "—"

        side = "BUY" if trade_type in ("BUY", "PURCHASE") else "SELL"

        return {
            "id": str(raw.get("id", raw.get("transactionHash", raw.get("txHash", "")))),
            "trader_address": trader_address,
            "event_type": "open",
            "side": side,
            "outcome": outcome,
            "price": price,
            "size": size,
            "usd_value": usd_value,
            "market_title": market_title,
            "market_url": market_url,
            "timestamp": timestamp,
        }

    def _process_positions(self, positions: List[Dict]) -> dict:
        result = {}
        for pos in positions:
            market_id = str(pos.get("conditionId", pos.get("market", {}).get("conditionId", pos.get("marketId", ""))))
            outcome = str(pos.get("outcome", pos.get("outcomeIndex", "")))
            size = float(pos.get("size", pos.get("currentValue", pos.get("shares", 0))))
            key = f"{market_id}:{outcome}".lower()
            if size > 0.01:
                result[key] = {
                    "size": size,
                    "market_id": market_id,
                    "outcome": outcome,
                    "market_title": pos.get("title", pos.get("market", {}).get("question", "")),
                    "market_slug": pos.get("slug", pos.get("market", {}).get("slug", "")),
                    "avg_price": float(pos.get("avgPrice", pos.get("price", 0))),
                }
        return result

    def _detect_closed(self, old: dict, new: dict, address: str) -> List[Dict]:
        closed = []
        for key, pos in old.items():
            if key not in new:
                slug = pos.get("market_slug", "")
                market_url = f"https://polymarket.com/event/{slug}" if slug else ""
                usd_value = pos.get("size", 0) * pos.get("avg_price", 0)
                closed.append({
                    "event_type": "close",
                    "trader_address": address,
                    "outcome": pos.get("outcome", ""),
                    "size": pos.get("size", 0),
                    "avg_price": pos.get("avg_price", 0),
                    "usd_value": usd_value,
                    "market_title": pos.get("market_title", ""),
                    "market_url": market_url,
                    "timestamp": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
                })
        return closed

    async def check_new_trades(self) -> List[Tuple[str, Dict]]:
        if not self.traders:
            return []

        notifications = []

        async with aiohttp.ClientSession() as session:
            for chat_id, trader_list in self.traders.items():
                for trader in trader_list:
                    address = trader["address"]
                    known_ids, old_positions = self._get_trader_data(chat_id, address)
                    known_ids_set = set(known_ids)

                    # --- Fetch trades ---
                    trades = await self.fetch_trades(address, session)
                    new_trades = []
                    current_ids = []

                    for t in trades:
                        tid = str(t.get("id", t.get("transactionHash", t.get("txHash", ""))))
                        if tid:
                            current_ids.append(tid)
                        if not known_ids_set:
                            continue
                        if tid and tid not in known_ids_set:
                            new_trades.append(t)

                    # --- Fetch positions ---
                    positions_raw = await self.fetch_positions(address, session)
                    new_positions = self._process_positions(positions_raw)

                    # --- Detect closed positions ---
                    closed_events = []
                    if old_positions:
                        closed_events = self._detect_closed(old_positions, new_positions, address)

                    # --- Save state ---
                    self._update_trader(chat_id, address, current_ids[:20], new_positions)

                    # --- Build notifications ---
                    for raw_trade in new_trades:
                        parsed = self._parse_activity(raw_trade, address)
                        notifications.append((chat_id, parsed))
                        logger.info(f"New trade for {address}: {parsed['id']} ${parsed['usd_value']:.2f}")

                    for close_event in closed_events:
                        notifications.append((chat_id, close_event))
                        logger.info(f"Closed position for {address}: {close_event['market_title']}")

                    await asyncio.sleep(0.5)

        return notifications

    async def get_positions_report(self, address: str) -> list:
        """Fetch positions with current price and PnL for hourly report."""
        async with aiohttp.ClientSession() as session:
            positions_raw = await self.fetch_positions(address, session)

        result = []
        for pos in positions_raw:
            size = float(pos.get("size", pos.get("shares", 0)))
            if size < 0.01:
                continue

            avg_price = float(pos.get("avgPrice", pos.get("price", 0)))
            cur_price = float(pos.get("currentPrice", pos.get("curPrice", avg_price)))
            current_value = size * cur_price
            invested = size * avg_price
            pnl = current_value - invested
            pnl_pct = (pnl / invested * 100) if invested > 0 else 0

            market_title = pos.get("title", pos.get("question", pos.get("market", {}).get("question", "")))
            market_slug = pos.get("slug", pos.get("market", {}).get("slug", ""))
            market_url = f"https://polymarket.com/event/{market_slug}" if market_slug else ""
            outcome = str(pos.get("outcome", pos.get("outcomeIndex", "")))

            result.append({
                "market_title": market_title,
                "market_url": market_url,
                "outcome": outcome,
                "size": size,
                "avg_price": avg_price,
                "current_price": cur_price,
                "current_value": current_value,
                "invested": invested,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
            })

        # Сортуємо по PnL (найбільший зверху)
        result.sort(key=lambda x: x["pnl"], reverse=True)
        return result
