import asyncio
import json
import logging
import os
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("BTMM_Engine")

HISTORY_LIMIT = 300
market_data: Dict[str, Dict] = {}
connected_clients: List[WebSocket] = []

TARGET_SYMBOLS = {
    "R_75": "V75",
    "1HZ75V": "V75 (1s)",
    "frxXAUUSD": "GOLD",
    "frxGBPUSD": "GBP/USD",
    "frxEURUSD": "EUR/USD",
    "frxAUDUSD": "AUD/USD"
}

# --- AUTOMATIC NODE.JS BRIDGE GENERATION ---

BRIDGE_SCRIPT = """
const WebSocket = require('ws');

const APP_ID = 1089;
const WS_URL = `wss://ws.derivws.com/websockets/v3?app_id=${APP_ID}`;

function connect() {
    const ws = new WebSocket(WS_URL, {
        headers: {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Origin': 'https://deriv.com'
        }
    });

    ws.on('open', () => {
        console.error("NODE_BRIDGE: Connected to Deriv WebSocket successfully!");
        
        const symbols = ["R_75", "1HZ75V", "frxXAUUSD", "frxGBPUSD", "frxEURUSD", "frxAUDUSD"];
        symbols.forEach((sym, index) => {
            setTimeout(() => {
                if (ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({
                        "ticks_history": sym,
                        "adjust_start_time": 1,
                        "count": 100,
                        "end": "latest",
                        "granularity": 900,
                        "style": "candles",
                        "subscribe": 1
                    }));
                }
            }, index * 300);
        });
    });

    ws.on('message', (data) => {
        process.stdout.write(data.toString() + '\\n');
    });

    ws.on('error', (err) => {
        console.error("NODE_BRIDGE_ERROR:", err.message);
    });

    ws.on('close', () => {
        console.error("NODE_BRIDGE: Connection closed. Reconnecting in 3s...");
        setTimeout(connect, 3000);
    });
}

connect();
"""

def ensure_bridge_file():
    if not os.path.exists("bridge.js"):
        with open("bridge.js", "w") as f:
            f.write(BRIDGE_SCRIPT.strip())
        logger.info("Generated 'bridge.js' Node.js gateway script.")


# --- TECHNICAL INDICATORS ---

def calculate_ema(prices: np.ndarray, period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    alpha = 2 / (period + 1)
    ema = prices[0]
    for price in prices[1:]:
        ema = (price * alpha) + (ema * (1 - alpha))
    return float(ema)


def calculate_rsi_series(prices: np.ndarray, period: int = 13) -> np.ndarray:
    if len(prices) <= period:
        return np.array([])
    
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    
    rsi_vals = []
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / (avg_loss if avg_loss > 0 else 1e-10)
        rsi_vals.append(100.0 - (100.0 / (1.0 + rs)))
        
    return np.array(rsi_vals)


def calculate_tdi(closes: np.ndarray):
    rsi_series = calculate_rsi_series(closes, period=13)
    if len(rsi_series) < 34:
        return None, None, None, None, None

    green_line = float(np.mean(rsi_series[-2:]))
    red_line = float(np.mean(rsi_series[-7:]))
    
    mbl_slice = rsi_series[-34:]
    yellow_line = float(np.mean(mbl_slice))
    
    std_dev = float(np.std(mbl_slice))
    upper_band = yellow_line + (1.6185 * std_dev)
    lower_band = yellow_line - (1.6185 * std_dev)

    return green_line, red_line, yellow_line, upper_band, lower_band


# --- BTMM EVALUATION ---

def evaluate_btmm_setup(candles: List[Dict], current_price: float, state: Dict) -> Tuple[str, str, int]:
    if len(candles) < 30:
        return "NEUTRAL", "BUILDING CANDLE DATA", 0

    closes = np.array([c["close"] for c in candles], dtype=np.float64)
    highs = np.array([c["high"] for c in candles], dtype=np.float64)
    lows = np.array([c["low"] for c in candles], dtype=np.float64)

    ema13 = calculate_ema(closes, 13)
    ema50 = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, min(200, len(closes)))

    green, red, yellow, vb_high, vb_low = calculate_tdi(closes)

    if not ema13 or not ema50 or not green or not yellow:
        return "NEUTRAL", "CALCULATING INDICATORS", 0

    state["ema13"] = round(ema13, 5)
    state["ema50"] = round(ema50, 5)
    state["ema200"] = round(ema200, 5) if ema200 else None
    state["tdi_green"] = round(green, 2)
    state["tdi_red"] = round(red, 2)
    state["tdi_yellow"] = round(yellow, 2)

    recent_h = np.max(highs[-12:])
    recent_l = np.min(lows[-12:])

    buy_score = 0
    sell_score = 0

    if current_price > ema13: buy_score += 15
    if ema13 > ema50: buy_score += 15
    if ema200 and current_price > ema200: buy_score += 10
    if green > red: buy_score += 15
    if green > yellow: buy_score += 15
    if green < 45: buy_score += 10
    if current_price <= recent_l: buy_score += 20

    if current_price < ema13: sell_score += 15
    if ema13 < ema50: sell_score += 15
    if ema200 and current_price < ema200: sell_score += 10
    if green < red: sell_score += 15
    if green < yellow: sell_score += 15
    if green > 55: sell_score += 10
    if current_price >= recent_h: sell_score += 20

    if buy_score >= 70:
        return "BUY", f"BTMM BULLISH CONFLUENCE ({buy_score}%)", buy_score
    elif sell_score >= 70:
        return "SELL", f"BTMM BEARISH CONFLUENCE ({sell_score}%)", sell_score
    elif buy_score >= 50:
        return "BUY_WATCH", f"BUILDING BUY SETUP ({buy_score}%)", buy_score
    elif sell_score >= 50:
        return "SELL_WATCH", f"BUILDING SELL SETUP ({sell_score}%)", sell_score

    return "NEUTRAL", "CONSOLIDATION RANGE", max(buy_score, sell_score)


async def process_data(sym: str, raw_event: dict):
    if sym not in market_data:
        market_data[sym] = {
            "candles": [], "ema13": None, "ema50": None, "ema200": None,
            "tdi_green": None, "tdi_red": None, "tdi_yellow": None,
            "signal": "NEUTRAL", "btmm_setup": "INITIALIZING", "score": 0,
            "last_price": 0.0,
        }

    state = market_data[sym]

    if "candles" in raw_event:
        state["candles"] = [
            {
                "timestamp": c["epoch"],
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"])
            }
            for c in raw_event["candles"]
        ]
        if state["candles"]:
            state["last_price"] = state["candles"][-1]["close"]

    elif "ohlc" in raw_event:
        ohlc = raw_event["ohlc"]
        c_time = ohlc["open_time"]
        price = float(ohlc["close"])

        new_c = {
            "timestamp": c_time,
            "open": float(ohlc["open"]),
            "high": float(ohlc["high"]),
            "low": float(ohlc["low"]),
            "close": price
        }

        candles = state["candles"]
        if candles and candles[-1]["timestamp"] == c_time:
            candles[-1] = new_c
        else:
            candles.append(new_c)
            if len(candles) > HISTORY_LIMIT:
                candles.pop(0)

        state["last_price"] = price

    if state["candles"]:
        signal, setup, score = evaluate_btmm_setup(state["candles"], state["last_price"], state)
        state["signal"] = signal
        state["btmm_setup"] = setup
        state["score"] = score

    payload = {
        "symbol": sym,
        "price": state["last_price"],
        "m15_candles": len(state["candles"]),
        "ema13": state.get("ema13"),
        "ema50": state.get("ema50"),
        "ema200": state.get("ema200"),
        "tdi_green": state.get("tdi_green"),
        "tdi_red": state.get("tdi_red"),
        "tdi_yellow": state.get("tdi_yellow"),
        "signal": state["signal"],
        "setup": state["btmm_setup"],
        "score": state["score"],
    }

    for client in connected_clients[:]:
        try:
            await client.send_json(payload)
        except Exception:
            if client in connected_clients:
                connected_clients.remove(client)


# --- UPDATED NODE.JS SUBPROCESS WORKER ---

async def node_bridge_worker():
    ensure_bridge_file()
    
    # Configure environment variables so Node can locate node_modules
    env = os.environ.copy()
    node_path = os.path.abspath("node_modules")
    env["NODE_PATH"] = f"{node_path}:{env.get('NODE_PATH', '')}"

    while True:
        logger.info("Starting Node.js bridge process...")
        try:
            process = await asyncio.create_subprocess_exec(
                "node", "bridge.js",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )

            async def read_stderr():
                while True:
                    err_line = await process.stderr.readline()
                    if not err_line:
                        break
                    logger.info(err_line.decode().strip())

            asyncio.create_task(read_stderr())

            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                
                try:
                    data = json.loads(line.decode().strip())
                    if "candles" in data and "echo_req" in data:
                        sym = data["echo_req"]["ticks_history"]
                        await process_data(sym, data)
                    elif "ohlc" in data:
                        sym = data["ohlc"]["symbol"]
                        await process_data(sym, data)
                except json.JSONDecodeError:
                    continue

        except Exception as e:
            logger.error(f"Node.js bridge error: {e}")
        
        logger.warning("Node process exited. Restarting in 3 seconds...")
        await asyncio.sleep(3)


# --- FASTAPI APP ---

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(node_bridge_worker())


html_content = """
<!DOCTYPE html>
<html>
<head>
    <title>BTMM Signal Monitor</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: monospace; background: #0e1117; color: #e0e0e0; padding: 15px; margin: 0; }
        h2 { color: #00e676; margin-bottom: 5px; }
        .sub-header { color: #bb86fc; font-size: 0.85rem; margin-bottom: 15px; }
        #cards-container { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }
        .card { background: #161b22; border-radius: 8px; padding: 14px; border: 1px solid #30363d; }
        .symbol-row { display: flex; justify-content: space-between; align-items: center; }
        .symbol { font-size: 1.1rem; font-weight: bold; color: #58a6ff; }
        .setup-tag { font-size: 0.75rem; background: #21262d; padding: 3px 8px; border-radius: 4px; color: #f0883e; border: 1px solid #f0883e; }
        .price { font-size: 1.5rem; margin: 8px 0; color: #ffffff; font-weight: bold; }
        .indicators { font-size: 0.8rem; color: #8b949e; line-height: 1.5; white-space: pre-line; }
        .signal { display: inline-block; padding: 5px 10px; border-radius: 4px; font-weight: bold; margin-top: 10px; font-size: 0.9rem; }
        .BUY { background: #0d381e; color: #3fb950; border: 1px solid #2ea043; }
        .SELL { background: #4c121a; color: #f85149; border: 1px solid #da3633; }
        .BUY_WATCH { background: #1a2a1d; color: #a3e635; border: 1px dashed #a3e635; }
        .SELL_WATCH { background: #33181c; color: #fb7185; border: 1px dashed #fb7185; }
        .NEUTRAL { background: #21262d; color: #8b949e; border: 1px solid #30363d; }
        .status { color: #8b949e; font-size: 0.8rem; margin-bottom: 12px; }
        .tdi-green { color: #3fb950; font-weight: bold; }
        .tdi-red { color: #f85149; font-weight: bold; }
        .tdi-yellow { color: #d29922; font-weight: bold; }
    </style>
</head>
<body>
    <h2>🏛️ BTMM Institutional Signal Engine</h2>
    <div class="sub-header">M15 Multi-Asset Monitor (V75, V75s, Gold, Forex)</div>
    <div class="status" id="ws-status">Connecting to Backend Engine...</div>
    <div id="cards-container"></div>

    <script>
        const assetMap = {
            "R_75": "V75",
            "1HZ75V": "V75 (1s)",
            "frxXAUUSD": "GOLD",
            "frxGBPUSD": "GBP/USD",
            "frxEURUSD": "EUR/USD",
            "frxAUDUSD": "AUD/USD"
        };

        const container = document.getElementById('cards-container');
        const statusEl = document.getElementById('ws-status');

        Object.keys(assetMap).forEach(sym => {
            const card = document.createElement('div');
            card.id = `card-${sym}`;
            card.className = 'card';
            card.innerHTML = `
                <div class="symbol-row">
                    <span class="symbol">${assetMap[sym]}</span>
                    <span class="setup-tag" id="${sym}-setup">WAITING FEED...</span>
                </div>
                <div class="price" id="${sym}-price">---</div>
                <div class="indicators" id="${sym}-ind">Fetching live ticks...</div>
                <div class="signal NEUTRAL" id="${sym}-sig">INITIALIZING</div>
            `;
            container.appendChild(card);
        });

        function connectLocal() {
            const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const ws = new WebSocket(`${proto}//${window.location.host}/ws/engine`);

            ws.onopen = () => {
                statusEl.innerText = "⚡ Connected to Engine Backend";
                statusEl.style.color = "#00e676";
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                const priceEl = document.getElementById(`${data.symbol}-price`);
                if (!priceEl) return;

                priceEl.innerText = `$${data.price}`;
                document.getElementById(`${data.symbol}-setup`).innerText = data.setup;

                const e13 = data.ema13 ?? '...';
                const e50 = data.ema50 ?? '...';
                const e200 = data.ema200 ?? '...';
                const g = data.tdi_green ?? '...';
                const r = data.tdi_red ?? '...';
                const y = data.tdi_yellow ?? '...';

                document.getElementById(`${data.symbol}-ind`).innerHTML = 
                    `M15 Candles Loaded: <b>${data.m15_candles}</b>\n` +
                    `EMAs (13/50/200): ${e13} / ${e50} / ${e200}\n` +
                    `TDI -> Green: <span class="tdi-green">${g}</span> | Red: <span class="tdi-red">${r}</span> | Yellow: <span class="tdi-yellow">${y}</span>`;

                const sigEl = document.getElementById(`${data.symbol}-sig`);
                sigEl.innerText = `${data.signal} (${data.score}%)`;
                sigEl.className = `signal ${data.signal}`;
            };

            ws.onclose = () => {
                statusEl.innerText = "⚠️ Backend connection lost. Reconnecting...";
                statusEl.style.color = "#f0883e";
                setTimeout(connectLocal, 2000);
            };
        }

        connectLocal();
    </script>
</body>
</html>
"""

@app.get("/")
async def get():
    return HTMLResponse(html_content)

@app.websocket("/ws/engine")
async def websocket_engine(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in connected_clients:
            connected_clients.remove(websocket)

