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

    async def fetch_usdc_balance(self, address: str, session: aiohttp.ClientSession) -> float:
        """Fetch USDC balance from Polygon blockchain via public RPC."""
        # USDC contract on Polygon
        USDC_CONTRACT = "0x2791bca1f2de4661ed88a30c99a7a9449aa84174"
        # balanceOf(address) function selector
        data = "0x70a08231000000000000000000000000" + address[2:].lower().zfill(64)

        rpc_endpoints = [
            "https://polygon-rpc.com",
            "https://rpc-mainnet.matic.network",
            "https://rpc.ankr.com/polygon",
        ]

        for rpc in rpc_endpoints:
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "method": "eth_call",
                    "params": [{"to": USDC_CONTRACT, "data": data}, "latest"],
                    "id": 1
                }
                async with session.post(rpc, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        hex_val = result.get("result", "0x0")
                        if hex_val and hex_val != "0x":
                            # USDC has 6 decimals
                            balance = int(hex_val, 16) / 1_000_000
                            logger.info(f"USDC balance for {address[:10]}: ${balance:.2f}")
                            return balance
            except Exception as e:
                logger.debug(f"RPC {rpc} failed: {e}")
                continue

        return 0.0


    async def fetch_portfolio_value(self, address: str, session: aiohttp.ClientSession) -> dict:
        """Fetch total portfolio value including USDC balance."""
        result = {"total": 0.0, "available": 0.0, "positions_value": 0.0}

        endpoints = [
            f"{POLYMARKET_DATA_API}/users/{address}",
            f"{POLYMARKET_DATA_API}/profile?user={address}",
            f"{POLYMARKET_GAMMA_API}/users?address={address}",
            f"{POLYMARKET_GAMMA_API}/profiles?address={address}",
        ]

        for url in endpoints:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, list) and data:
                            data = data[0]
                        logger.info(f"Portfolio {url}: {str(data)[:300]}")
                        total = float(data.get("portfolioValue",
                                      data.get("portfolio_value",
                                      data.get("totalValue",
                                      data.get("total", 0)))))
                        available = float(data.get("availableBalance",
                                          data.get("available_balance",
                                          data.get("cashBalance",
                                          data.get("usdc", 0)))))
                        if total > 0:
                            result = {"total": total, "available": available, "positions_value": 0.0}
                            return result
            except Exception as e:
                logger.debug(f"Portfolio {url} failed: {e}")

        # Fallback — рахуємо з позицій
        try:
            positions_raw = await self.fetch_positions(address, session)
            pos_value = sum(
                float(p.get("size", 0)) * float(p.get("currentPrice", p.get("curPrice", 0)))
                for p in positions_raw
                if float(p.get("currentPrice", p.get("curPrice", 0))) > 0
            )
            result["positions_value"] = pos_value
        except Exception:
            pass

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

        # Баланс USDC з блокчейну + вартість позицій = загальний баланс
        async with aiohttp.ClientSession() as session2:
            usdc_balance = await self.fetch_usdc_balance(address, session2)

        total_balance = usdc_balance + open_value

        return {
            "pnl_month": pnl_month,
            "pnl_alltime": pnl_alltime,
            "month_name": f"{month_name_ua[now.month]} {now.year}",
            "open_value": open_value,
            "usdc_balance": usdc_balance,
            "total_balance": total_balance,
        }


# ============ METAR MONITOR ============

METAR_API = "https://aviationweather.gov/api/data/metar"

# Станції для моніторингу виправлень (без сповіщень про кожне оновлення)
CORRECTION_WATCH_STATIONS = [
    "NZWN", "WSSS", "EGLC", "RCSS", "RKSI", "ZUCK", "ZSQD", "WMKK",
    "EFHK", "ZSPD", "ZGGG", "VILK", "EHAM", "LFPB", "LTAC", "FACT",
    "EDDM", "RKPK", "LEMD", "ZGSZ", "LTFM", "KMIA", "ZUUU", "EPWA",
    "SBGR", "LLBG", "ZBAA", "LIMC", "OPKC", "KAUS", "OEJN", "KORD",
    "KLGA", "CYYZ", "SAEZ", "KATL", "KLAX", "KSFO", "KBKF", "ZHHH",
    "KHOU", "MPMG", "KSEA", "MMMX"
]

METAR_DATA_FILE = os.path.join(DATA_DIR, "metar_stations.json")


class MetarMonitor:
    def __init__(self):
        self.stations = {}  # {chat_id: [{code, last_metar_time}]}
        self._load()

    def _load(self):
        if os.path.exists(METAR_DATA_FILE):
            try:
                with open(METAR_DATA_FILE, "r") as f:
                    self.stations = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load metar stations: {e}")

    def _save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(METAR_DATA_FILE, "w") as f:
                json.dump(self.stations, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save metar stations: {e}")

    def add_station(self, chat_id: str, code: str) -> str:
        code = code.upper()
        if chat_id not in self.stations:
            self.stations[chat_id] = []
        for s in self.stations[chat_id]:
            if s["code"] == code:
                return "exists"
        self.stations[chat_id].append({
            "code": code,
            "last_metar_time": None,
            "last_temp_c": None,    # для виявлення виправлень
            "last_temp_f": None,
        })
        self._save()
        return "added"

    def remove_station(self, chat_id: str, code: str):
        code = code.upper()
        if chat_id in self.stations:
            self.stations[chat_id] = [
                s for s in self.stations[chat_id] if s["code"] != code
            ]
            self._save()

    def get_stations(self, chat_id: str) -> List[Dict]:
        return self.stations.get(chat_id, [])

    def get_total_stations(self) -> int:
        return sum(len(v) for v in self.stations.values())

    def _parse_metar_temp(self, raw: str):
        import re
        # Точний формат T01720094
        t_match = re.search(r'T(\d{4})(\d{4})', raw)
        if t_match:
            temp_raw = t_match.group(1)
            sign = -1 if temp_raw[0] == '1' else 1
            temp_c = sign * int(temp_raw[1:]) / 10.0
            temp_f = temp_c * 9/5 + 32
            return temp_f, temp_c
        # Стандартний TT/DD
        import re
        temp_match = re.search(r'\b(M?\d{2})/(M?\d{2})\b', raw)
        if temp_match:
            temp_str = temp_match.group(1)
            sign = -1 if temp_str.startswith('M') else 1
            temp_c = sign * int(temp_str.replace('M', ''))
            temp_f = temp_c * 9/5 + 32
            return temp_f, temp_c
        return None

    def _parse_metar_time(self, raw: str) -> str:
        import re
        time_match = re.search(r'\d{6}Z', raw)
        if time_match:
            t = time_match.group(0)
            return f"{t[2:4]}:{t[4:6]} UTC"
        return ""

    async def fetch_metar(self, station: str) -> Dict:
        station = station.upper()
        try:
            async with aiohttp.ClientSession() as session:
                params = {"ids": station, "format": "raw", "taf": "false", "hours": "1"}
                async with session.get(METAR_API, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    text = await resp.text()
                    if not text.strip():
                        return None

                    # Об'єднуємо багаторядкові METAR (MMMX та інші розбивають на 2 рядки)
                    raw_lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
                    merged = []
                    current = ""
                    for line in raw_lines:
                        upper = line.upper()
                        if upper.startswith("METAR") or upper.startswith("SPECI"):
                            if current:
                                merged.append(current)
                            current = line
                        else:
                            current = (current + " " + line).strip() if current else line
                    if current:
                        merged.append(current)
                    if not merged:
                        merged = raw_lines
                    metar_line = ""
                    for line in merged:
                        if station.upper() in line.upper():
                            metar_line = line
                            break
                    if not metar_line:
                        metar_line = merged[0] if merged else ""
                    if not metar_line:
                        return None

                    result = self._parse_metar_temp(metar_line)
                    if result is None:
                        return None

                    temp_f, temp_c = result
                    time_str = self._parse_metar_time(metar_line)

                    return {
                        "temp_f": temp_f,
                        "temp_c": temp_c,
                        "time": time_str,
                        "raw": metar_line,
                        "metar_time": time_str,
                    }
        except Exception as e:
            logger.error(f"Error fetching METAR for {station}: {e}")
            return None

    async def check_updates(self) -> List[Tuple]:
        """
        Перевіряє METAR оновлення.
        Повертає (chat_id, code, data, event_type)
        event_type: "new" = нові дані, "correction" = виправлення
        """
        if not self.stations:
            return []

        notifications = []

        # Унікальні станції
        unique = {}
        for chat_id, station_list in self.stations.items():
            for s in station_list:
                code = s["code"]
                if code not in unique:
                    unique[code] = []
                unique[code].append((chat_id, s))

        for code, subscribers in unique.items():
            data = await self.fetch_metar(code)
            if not data:
                await asyncio.sleep(0.3)
                continue

            new_time = data["metar_time"]
            new_temp_c = data["temp_c"]
            new_temp_f = data["temp_f"]

            for chat_id, station_data in subscribers:
                last_time = station_data.get("last_metar_time")
                last_temp_c = station_data.get("last_temp_c")

                if last_time is None:
                    # Перший запуск — просто зберігаємо
                    station_data["last_metar_time"] = new_time
                    station_data["last_temp_c"] = new_temp_c
                    station_data["last_temp_f"] = new_temp_f
                    continue

                if new_time != last_time:
                    # Новий METAR (час змінився)
                    notifications.append((chat_id, code, data, "new"))
                    station_data["last_metar_time"] = new_time
                    station_data["last_temp_c"] = new_temp_c
                    station_data["last_temp_f"] = new_temp_f

                elif new_time == last_time and last_temp_c is not None:
                    # Той самий час — перевіряємо чи змінилась температура
                    if abs(new_temp_c - last_temp_c) >= 0.5:
                        # ВИПРАВЛЕННЯ! Той самий час але інша температура
                        correction_data = {
                            **data,
                            "old_temp_c": last_temp_c,
                            "old_temp_f": station_data.get("last_temp_f", last_temp_c * 9/5 + 32),
                        }
                        notifications.append((chat_id, code, correction_data, "correction"))
                        station_data["last_temp_c"] = new_temp_c
                        station_data["last_temp_f"] = new_temp_f
                        logger.info(f"METAR correction detected for {code}: {last_temp_c}°C -> {new_temp_c}°C at {new_time}")

            await asyncio.sleep(0.3)

        self._save()
        return notifications


class CorrectionMonitor:
    """
    Моніторить фіксований список станцій тільки для виявлення виправлень METAR.
    Не надсилає сповіщень про нові оновлення — тільки про виправлення.
    """
    def __init__(self):
        self.data_file = os.path.join(DATA_DIR, "correction_data.json")
        # {station_code: {metar_time: str, temp_c: float, temp_f: float}}
        self.station_data = {}
        # {chat_id: True} — хто підписаний на алерти виправлень
        self.subscribers_file = os.path.join(DATA_DIR, "correction_subscribers.json")
        self.subscribers = {}
        self._last_check = None
        self._total_corrections = 0
        self._load()

    def _load(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r") as f:
                    self.station_data = json.load(f)
            except Exception:
                self.station_data = {}
        if os.path.exists(self.subscribers_file):
            try:
                with open(self.subscribers_file, "r") as f:
                    self.subscribers = json.load(f)
            except Exception:
                self.subscribers = {}

    def _save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(self.data_file, "w") as f:
                json.dump(self.station_data, f, indent=2)
            with open(self.subscribers_file, "w") as f:
                json.dump(self.subscribers, f, indent=2)
        except Exception as e:
            logger.error(f"CorrectionMonitor save error: {e}")

    def get_stats(self) -> dict:
        """Статистика моніторингу виправлень."""
        return {
            "total_stations": len(CORRECTION_WATCH_STATIONS),
            "checked_stations": len(self.station_data),
            "last_check": self._last_check,
            "total_corrections": self._total_corrections,
        }

    def subscribe(self, chat_id: str):
        """Підписати чат на алерти виправлень."""
        self.subscribers[chat_id] = True
        self._save()

    def unsubscribe(self, chat_id: str):
        self.subscribers.pop(chat_id, None)
        self._save()

    def is_subscribed(self, chat_id: str) -> bool:
        return self.subscribers.get(chat_id, False)

    async def check_corrections(self, metar_instance) -> List[Tuple]:
        """
        Перевіряє всі 44 станції на виправлення.
        Повертає (chat_id, station, correction_data) для всіх підписників.
        """
        if not self.subscribers:
            return []

        notifications = []

        for code in CORRECTION_WATCH_STATIONS:
            data = await metar_instance.fetch_metar(code)
            if not data:
                await asyncio.sleep(0.2)
                continue

            new_time = data["metar_time"]
            new_temp_c = data["temp_c"]
            new_temp_f = data["temp_f"]

            old = self.station_data.get(code)

            if old is None:
                # Перший запуск — зберігаємо без сповіщення
                self.station_data[code] = {
                    "metar_time": new_time,
                    "temp_c": new_temp_c,
                    "temp_f": new_temp_f,
                }
                continue

            old_time = old.get("metar_time")
            old_temp_c = old.get("temp_c")
            old_temp_f = old.get("temp_f")

            if new_time == old_time and old_temp_c is not None:
                # Той самий час — перевіряємо температуру
                if abs(new_temp_c - old_temp_c) >= 0.5:
                    logger.info(f"CORRECTION: {code} {old_temp_c}°C -> {new_temp_c}°C at {new_time}")
                    correction_data = {
                        **data,
                        "old_temp_c": old_temp_c,
                        "old_temp_f": old_temp_f,
                    }
                    for chat_id in self.subscribers:
                        notifications.append((chat_id, code, correction_data))
                    # Оновлюємо
                    self.station_data[code]["temp_c"] = new_temp_c
                    self.station_data[code]["temp_f"] = new_temp_f

            elif new_time != old_time:
                # Новий METAR — просто оновлюємо дані
                self.station_data[code] = {
                    "metar_time": new_time,
                    "temp_c": new_temp_c,
                    "temp_f": new_temp_f,
                }

            await asyncio.sleep(0.2)

        from datetime import datetime, timezone
        self._last_check = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        self._total_corrections += len(notifications)
        self._save()
        return notifications


# ============ PEAK TEMPERATURE FORECAST MONITOR ============

OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"

try:
    from stations_data import STATIONS_META
except ImportError:
    STATIONS_META = {}


class PeakForecastMonitor:
    """
    Перевіряє прогноз температури для 44 станцій.
    Надсилає алерт якщо максимум температури очікується
    в перші 6 годин місцевого часу доби.
    Алерт надсилається за 3-4 години до опівночі місцевого часу.
    """

    def __init__(self):
        self.data_file = os.path.join(DATA_DIR, "peak_forecast.json")
        # {station: {date: alerted}} — щоб не спамити
        self.alerted = {}
        self.subscribers = {}
        self.subscribers_file = os.path.join(DATA_DIR, "peak_subscribers.json")
        self._load()

    def _load(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r") as f:
                    self.alerted = json.load(f)
            except Exception:
                self.alerted = {}
        if os.path.exists(self.subscribers_file):
            try:
                with open(self.subscribers_file, "r") as f:
                    self.subscribers = json.load(f)
            except Exception:
                self.subscribers = {}

    def _save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(self.data_file, "w") as f:
                json.dump(self.alerted, f, indent=2)
            with open(self.subscribers_file, "w") as f:
                json.dump(self.subscribers, f, indent=2)
        except Exception as e:
            logger.error(f"PeakForecastMonitor save error: {e}")

    def subscribe(self, chat_id: str):
        self.subscribers[chat_id] = True
        self._save()

    def unsubscribe(self, chat_id: str):
        self.subscribers.pop(chat_id, None)
        self._save()

    def is_subscribed(self, chat_id: str) -> bool:
        return self.subscribers.get(chat_id, False)

    async def fetch_hourly_forecast(self, lat: float, lon: float, tz: str, session: aiohttp.ClientSession) -> list:
        """Отримує погодинний прогноз з Open-Meteo."""
        try:
            params = {
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m",
                "temperature_unit": "celsius",
                "timezone": tz,
                "forecast_days": 2
            }
            async with session.get(OPEN_METEO_API, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    times = data["hourly"]["time"]
                    temps = data["hourly"]["temperature_2m"]
                    return list(zip(times, temps))
        except Exception as e:
            logger.error(f"Open-Meteo error: {e}")
        return []

    def _find_peak_in_first_6h(self, hourly: list, target_date: str) -> dict:
        """
        Перевіряє чи максимум температури припадає на перші 6 годин доби.
        target_date формат: "2026-06-25"
        """
        # Всі години цього дня
        day_hours = [(t, temp) for t, temp in hourly if t.startswith(target_date)]
        if not day_hours:
            return None

        # Знаходимо максимум за день
        max_temp = max(temp for _, temp in day_hours)
        max_time = next(t for t, temp in day_hours if temp == max_temp)
        max_hour = int(max_time[11:13])

        # Перевіряємо чи максимум в перші 6 годин (00:00-05:59)
        is_early_peak = max_hour < 6

        return {
            "max_temp": max_temp,
            "max_time": max_time[11:16],
            "max_hour": max_hour,
            "is_early_peak": is_early_peak,
            "all_day": day_hours,
        }

    async def check_forecasts(self) -> List[Tuple]:
        """
        Перевіряє всі станції.
        Надсилає алерт за 3-4 години до опівночі місцевого часу
        якщо максимум очікується в перші 6 годин.
        """
        if not self.subscribers or not STATIONS_META:
            return []

        from datetime import datetime, timezone, timedelta
        import pytz

        notifications = []
        now_utc = datetime.now(timezone.utc)

        async with aiohttp.ClientSession() as session:
            for code, meta in STATIONS_META.items():
                try:
                    tz_str = meta["tz"]
                    local_tz = pytz.timezone(tz_str)
                    now_local = now_utc.astimezone(local_tz)

                    # Перевіряємо чи зараз 20:00-21:00 місцевого (за 3-4 години до опівночі)
                    local_hour = now_local.hour
                    if local_hour not in (20, 21):
                        continue

                    # Завтрашня дата (місцева)
                    tomorrow = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
                    alert_key = f"{code}_{tomorrow}"

                    # Вже надсилали алерт сьогодні?
                    if self.alerted.get(alert_key):
                        continue

                    # Отримуємо прогноз
                    hourly = await self.fetch_hourly_forecast(
                        meta["lat"], meta["lon"], tz_str, session
                    )
                    if not hourly:
                        continue

                    result = self._find_peak_in_first_6h(hourly, tomorrow)
                    if not result:
                        continue

                    if result["is_early_peak"]:
                        # Знаходимо температуру вдень для порівняння
                        day_temps = [temp for t, temp in result["all_day"] if 10 <= int(t[11:13]) <= 18]
                        day_max = max(day_temps) if day_temps else result["max_temp"]

                        alert_data = {
                            "station": code,
                            "city": meta["name"],
                            "date": tomorrow,
                            "max_temp": result["max_temp"],
                            "max_time": result["max_time"],
                            "day_max": day_max,
                        }

                        for chat_id in self.subscribers:
                            notifications.append((chat_id, alert_data))

                        self.alerted[alert_key] = True
                        logger.info(f"Peak forecast alert for {code}: max {result['max_temp']}°C at {result['max_time']}")

                except Exception as e:
                    logger.error(f"Peak forecast error for {code}: {e}")

                await asyncio.sleep(0.3)

        self._save()
        return notifications
