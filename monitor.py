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
            "last_trade_timestamp": None,
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

    def _update_last_trade(self, chat_id: str, address: str, trade_ids: List[str], timestamp: str):
        if chat_id in self.traders:
            for t in self.traders[chat_id]:
                if t["address"] == address:
                    t["last_trade_ids"] = trade_ids
                    t["last_trade_timestamp"] = timestamp
        self._save()

    def _update_positions(self, chat_id: str, address: str, positions: dict):
        if chat_id in self.traders:
            for t in self.traders[chat_id]:
                if t["address"] == address:
                    t["positions"] = positions
        self._save()

    def _get_positions(self, chat_id: str, address: str) -> dict:
        if chat_id in self.traders:
            for t in self.traders[chat_id]:
                if t["address"] == address:
                    return t.get("positions", {})
        return {}

    async def fetch_recent_trades(self, address: str, session: aiohttp.ClientSession) -> List[Dict]:
        try:
            url = f"{POLYMARKET_CLOB_API}/trades"
            params = {"maker_address": address, "limit": 20}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", [])
                else:
                    logger.warning(f"CLOB API returned {resp.status} for {address}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching trades for {address}: {e}")
            return []

    async def fetch_open_positions(self, address: str, session: aiohttp.ClientSession) -> List[Dict]:
        try:
            url = f"{POLYMARKET_GAMMA_API}/positions"
            params = {"user": address, "sizeThreshold": "0.01"}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        return data
                    return data.get("data", data.get("positions", []))
                else:
                    logger.warning(f"Positions API returned {resp.status} for {address}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching positions for {address}: {e}")
            return []

    async def fetch_market_info(self, condition_id: str, session: aiohttp.ClientSession) -> Dict:
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
        taker_addr = raw.get("taker_address", "").lower()
        price = float(raw.get("price", 0))
        size = float(raw.get("size", 0))
        usd_value = price * size

        raw_side = raw.get("side", "BUY")
        if taker_addr == trader_address and raw_side.upper() == "BUY":
            side = "SELL"
        elif taker_addr == trader_address and raw_side.upper() == "SELL":
            side = "BUY"
        else:
            side = raw_side

        outcome = raw.get("outcome", "Yes")
        market_slug = market_info.get("slug", "")
        market_title = market_info.get("question", market_info.get("title", ""))
        market_url = f"https://polymarket.com/event/{market_slug}" if market_slug else ""

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
            "event_type": "open",
        }

    def _build_position_key(self, condition_id: str, outcome: str) -> str:
        return f"{condition_id}:{outcome}".lower()

    def _process_positions(self, current_positions: List[Dict]) -> dict:
        new_positions = {}
        for pos in current_positions:
            condition_id = pos.get("conditionId", pos.get("condition_id", ""))
            outcome = str(pos.get("outcome", pos.get("outcomeIndex", "")))
            size = float(pos.get("size", pos.get("currentValue", 0)))
            key = self._build_position_key(condition_id, outcome)
            if size > 0.01:
                new_positions[key] = {
                    "size": size,
                    "condition_id": condition_id,
                    "outcome": outcome,
                    "market_title": pos.get("title", pos.get("question", "")),
                    "market_url": pos.get("market_url", ""),
                    "avg_price": float(pos.get("avgPrice", pos.get("price", 0))),
                }
        return new_positions

    def _detect_closed_positions(self, old_positions: dict, new_positions: dict, address: str) -> List[Dict]:
        closed = []
        for key, old_pos in old_positions.items():
            if key not in new_positions:
                closed.append({
                    "event_type": "close",
                    "trader_address": address,
                    "outcome": old_pos.get("outcome", ""),
                    "size": old_pos.get("size", 0),
                    "avg_price": old_pos.get("avg_price", 0),
                    "usd_value": old_pos.get("size", 0) * old_pos.get("avg_price", 0),
                    "market_title": old_pos.get("market_title", ""),
                    "market_url": old_pos.get("market_url", ""),
                    "timestamp": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
                    "condition_id": old_pos.get("condition_id", ""),
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
                    known_ids = set(trader.get("last_trade_ids", []))

                    # --- New trades ---
                    trades = await self.fetch_recent_trades(address, session)
                    new_trades = []
                    current_ids = []

                    for t in trades:
                        tid = t.get("id", t.get("trade_id", ""))
                        current_ids.append(tid)
                        if not known_ids:
                            continue
                        if tid not in known_ids:
                            new_trades.append(t)

                    if current_ids:
                        self._update_last_trade(
                            chat_id, address, current_ids[:10],
                            trades[0].get("match_time", "") if trades else ""
                        )

                    # Fetch market info for new trades
                    market_cache = {}
                    for t in new_trades:
                        cid = t.get("condition_id", "")
                        if cid and cid not in market_cache:
                            market_cache[cid] = await self.fetch_market_info(cid, session)
                            await asyncio.sleep(0.2)

                    for raw_trade in new_trades:
                        cid = raw_trade.get("condition_id", "")
                        parsed = self._parse_trade(raw_trade, market_cache.get(cid, {}), address)
                        notifications.append((chat_id, parsed))
                        logger.info(f"New trade for {address}: {parsed['id']}")

                    # --- Closed positions ---
                    current_positions = await self.fetch_open_positions(address, session)
                    new_pos_map = self._process_positions(current_positions)
                    old_pos_map = self._get_positions(chat_id, address)

                    if old_pos_map:
                        closed = self._detect_closed_positions(old_pos_map, new_pos_map, address)
                        for close_event in closed:
                            notifications.append((chat_id, close_event))
                            logger.info(f"Position closed for {address}: {close_event['market_title']}")

                    self._update_positions(chat_id, address, new_pos_map)

                    await asyncio.sleep(0.5)

        return notifications
