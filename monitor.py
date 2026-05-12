import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Tuple, Dict
import aiohttp

logger = logging.getLogger(__name__)

# Постійне сховище на Railway Volume
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
DATA_FILE = os.path.join(DATA_DIR, "traders.json")

POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com"
POLYMARKET_DATA_API = "https://data-api.polymarket.com"


class PolymarketMonitor:
    def __init__(self):
        self.traders: Dict[str, List[Dict]] = {}
        os.makedirs(DATA_DIR, exist_ok=True)
        self._load()

    def _load(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r") as f:
                    self.traders = json.load(f)
                logger.info(f"Loaded {self.get_total_traders()} traders from {DATA_FILE}")
            except Exception as e:
                logger.error(f"Failed to load traders: {e}")
                self.traders = {}

    def _save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(DATA_FILE, "w") as f:
                json.dump(self.traders, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save traders: {e}")

    def add_trader(self, chat_id: str, address: str, nickname: str = "", is_own: bool = False) -> str:
        address = address.lower()
        if chat_id not in self.traders:
            self.traders[chat_id] = []
        for t in self.traders[chat_id]:
            if t["address"] == address:
                return "exists"
        self.traders[chat_id].append({
            "address": address,
            "nickname": nickname,
            "is_own": is_own,  # True = мій акаунт, False = відстежуваний
            "last_trade_ids": [],
            "positions": {}
        })
        self._save()
        return "added"

    def set_own_account(self, chat_id: str, address: str):
        """Позначити адресу як власний акаунт."""
        address = address.lower()
        if chat_id in self.traders:
            for t in self.traders[chat_id]:
                if t["address"] == address:
                    t["is_own"] = True
                    self._save()
                    return True
        return False

    def get_own_accounts(self, chat_id: str) -> List[Dict]:
        return [t for t in self.traders.get(chat_id, []) if t.get("is_own", False)]

    def get_watched_traders(self, chat_id: str) -> List[Dict]:
        return [t for t in self.traders.get(chat_id, []) if not t.get("is_own", False)]

    def set_nickname(self, chat_id: str, address: str, nickname: str):
        address = address.lower()
        if chat_id in self.traders:
            for t in self.traders[chat_id]:
                if t["address"] == address:
                    t["nickname"] = nickname
                    self._save()
                    return True
        return False

    def get_nickname(self, chat_id: str, address: str) -> str:
        address = address.lower()
        if chat_id in self.traders:
            for t in self.traders[chat_id]:
                if t["address"] == address:
                    nick = t.get("nickname", "")
                    return nick if nick else f"{address[:10]}...{address[-6:]}"
        return f"{address[:10]}...{address[-6:]}"

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
        try:
            url = f"{POLYMARKET_DATA_API}/activity"
            params = {"user": address, "limit": 20, "offset": 0}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else data.get("data", [])
                logger.warning(f"activity {resp.status} for {address}")
                return []
        except Exception as e:
            logger.error(f"Error fetching trades: {e}")
            return []

    async def fetch_positions(self, address: str, session: aiohttp.ClientSession) -> List[Dict]:
        try:
            url = f"{POLYMARKET_DATA_API}/positions"
            params = {"user": address, "sizeThreshold": "0.01", "limit": 50}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else data.get("data", [])
                logger.warning(f"positions {resp.status} for {address}")
                return []
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    def _parse_activity(self, raw: Dict, trader_address: str) -> Dict:
        trade_type = raw.get("type", raw.get("side", "")).upper()
        side = "BUY" if trade_type in ("BUY", "PURCHASE", "BUY_OUTCOME") else "SELL"
        outcome = raw.get("outcome", raw.get("outcomeIndex", ""))
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
        return {
            "id": str(raw.get("id", raw.get("transactionHash", raw.get("txHash", "")))),
            "trader_address": trader_address,
            "event_type": "open",
            "side": side,
            "outcome": str(outcome),
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
            market_id = str(pos.get("conditionId", pos.get("market", {}).get("conditionId", "")))
            outcome = str(pos.get("outcome", pos.get("outcomeIndex", "")))
            size = float(pos.get("size", pos.get("shares", 0)))
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
                closed.append({
                    "event_type": "close",
                    "trader_address": address,
                    "outcome": pos.get("outcome", ""),
                    "size": pos.get("size", 0),
                    "avg_price": pos.get("avg_price", 0),
                    "usd_value": pos.get("size", 0) * pos.get("avg_price", 0),
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
                    positions_raw = await self.fetch_positions(address, session)
                    new_positions = self._process_positions(positions_raw)
                    closed_events = []
                    if old_positions:
                        closed_events = self._detect_closed(old_positions, new_positions, address)
                    self._update_trader(chat_id, address, current_ids[:20], new_positions)
                    for raw_trade in new_trades:
                        parsed = self._parse_activity(raw_trade, address)
                        notifications.append((chat_id, parsed))
                    for close_event in closed_events:
                        notifications.append((chat_id, close_event))
                    await asyncio.sleep(0.5)
        return notifications

    async def get_positions_report(self, address: str) -> list:
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
            # Фільтр резолвнутих ринків
            if cur_price == 0 or current_value < 0.01:
                continue
            pnl = current_value - invested
            pnl_pct = (pnl / invested * 100) if invested > 0 else 0
            market_title = pos.get("title", pos.get("question", pos.get("market", {}).get("question", "")))
            market_slug = pos.get("slug", pos.get("market", {}).get("slug", ""))
            market_url = f"https://polymarket.com/event/{market_slug}" if market_slug else ""
            result.append({
                "market_title": market_title,
                "market_url": market_url,
                "outcome": str(pos.get("outcome", pos.get("outcomeIndex", ""))),
                "size": size,
                "avg_price": avg_price,
                "current_price": cur_price,
                "current_value": current_value,
                "invested": invested,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
            })
        result.sort(key=lambda x: x["pnl"], reverse=True)
        return result

    async def fetch_all_activity(self, address: str, session: aiohttp.ClientSession, limit: int = 500) -> List[Dict]:
        """Fetch full activity history for PnL calculation."""
        all_trades = []
        offset = 0
        batch = 100
        while offset < limit:
            try:
                url = f"{POLYMARKET_DATA_API}/activity"
                params = {"user": address, "limit": batch, "offset": offset}
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        break
                    data = await resp.json()
                    batch_data = data if isinstance(data, list) else data.get("data", [])
                    if not batch_data:
                        break
                    all_trades.extend(batch_data)
                    if len(batch_data) < batch:
                        break
                    offset += batch
                    await asyncio.sleep(0.3)
            except Exception as e:
                logger.error(f"Error fetching activity batch: {e}")
                break
        return all_trades

    def _parse_timestamp(self, raw) -> datetime:
        try:
            if isinstance(raw, (int, float)):
                return datetime.fromtimestamp(raw, tz=timezone.utc)
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return datetime.now(timezone.utc)

    def _calc_trade_pnl(self, trade: Dict) -> float:
        """
        Calculate PnL contribution of a single trade.
        Типи: TRADE (купівля/продаж), REDEEM (виплата виграшу)
        Ігноруємо: YIELD, DEPOSIT, WITHDRAWAL та інше
        """
        trade_type = trade.get("type", "").upper()

        # Ігноруємо все крім торгових операцій
        if trade_type not in ("TRADE", "REDEEM"):
            return 0

        usd = float(trade.get("usdcSize", trade.get("amount", 0)))

        if trade_type == "REDEEM":
            # Виплата виграшу = отримали гроші
            return usd

        if trade_type == "TRADE":
            # Для TRADE дивимось side: BUY = витратили, SELL = отримали
            side = trade.get("side", trade.get("tradeType", "")).upper()
            if side in ("SELL", "SELL_OUTCOME"):
                return usd   # отримали
            elif side in ("BUY", "BUY_OUTCOME"):
                return -usd  # витратили

        return 0

    async def get_pnl_stats(self, address: str) -> dict:
        """
        Повертає PnL за поточний місяць і за весь час.
        Логіка: сума всіх SELL/REDEEM мінус сума всіх BUY + поточна вартість позицій.
        """
        async with aiohttp.ClientSession() as session:
            all_activity = await self.fetch_all_activity(address, session)
            positions_raw = await self.fetch_positions(address, session)

        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        pnl_alltime = 0.0
        pnl_month = 0.0

        for trade in all_activity:
            trade_type = trade.get("type", "").upper()
            # Рахуємо тільки TRADE і REDEEM, решту ігноруємо
            if trade_type not in ("TRADE", "REDEEM"):
                continue

            contribution = self._calc_trade_pnl(trade)
            if contribution == 0:
                continue

            ts_raw = trade.get("timestamp", trade.get("createdAt", trade.get("created_at", "")))
            trade_dt = self._parse_timestamp(ts_raw)

            pnl_alltime += contribution
            if trade_dt >= month_start:
                pnl_month += contribution

        # Додаємо поточну вартість відкритих позицій
        open_value = 0.0
        for pos in positions_raw:
            size = float(pos.get("size", pos.get("shares", 0)))
            cur_price = float(pos.get("currentPrice", pos.get("curPrice", 0)))
            if size > 0.01 and cur_price > 0:
                open_value += size * cur_price

        pnl_alltime += open_value
        pnl_month += open_value  # поточні позиції впливають і на місячний PnL

        month_name_ua = {
            1: "Січень", 2: "Лютий", 3: "Березень", 4: "Квітень",
            5: "Травень", 6: "Червень", 7: "Липень", 8: "Серпень",
            9: "Вересень", 10: "Жовтень", 11: "Листопад", 12: "Грудень"
        }

        return {
            "pnl_month": pnl_month,
            "pnl_alltime": pnl_alltime,
            "month_name": f"{month_name_ua[now.month]} {now.year}",
            "open_value": open_value,
        }
