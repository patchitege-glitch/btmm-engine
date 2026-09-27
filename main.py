import os
import json
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="BTMM Institutional Signal Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Embedded Web UI HTML/JS template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BTMM Institutional Signal Engine</title>
    <style>
        :root {
            --bg-color: #0b0e14;
            --card-bg: #151921;
            --accent-green: #00e676;
            --accent-red: #ff5252;
            --accent-orange: #ff9100;
            --text-main: #e0e6ed;
            --text-muted: #78909c;
            --border-color: #263238;
        }
        body {
            background-color: var(--bg-color);
            color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 16px;
        }
        .header {
            margin-bottom: 20px;
        }
        .header h1 {
            font-size: 1.4rem;
            color: var(--accent-green);
            margin: 0 0 4px 0;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .header p {
            font-size: 0.85rem;
            color: var(--text-muted);
            margin: 0;
        }
        .status-banner {
            background: rgba(255, 145, 0, 0.1);
            border: 1px solid var(--accent-orange);
            color: var(--accent-orange);
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 0.8rem;
            margin-bottom: 16px;
            display: none;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
        }
        .card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 16px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }
        .asset-title {
            font-size: 1.1rem;
            font-weight: bold;
            color: #64b5f6;
        }
        .feed-badge {
            font-size: 0.7rem;
            padding: 2px 6px;
            border-radius: 4px;
            background: #263238;
            color: var(--accent-orange);
            border: 1px solid var(--accent-orange);
            text-transform: uppercase;
        }
        .feed-badge.live {
            color: var(--accent-green);
            border-color: var(--accent-green);
        }
        .price {
            font-size: 1.5rem;
            font-weight: 700;
            font-family: monospace;
            margin-bottom: 8px;
        }
        .sub-text {
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-bottom: 12px;
        }
        .signal-pill {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 700;
            background: #263238;
            color: var(--text-muted);
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🏛️ BTMM Institutional Signal Engine</h1>
        <p>M15 Multi-Asset Monitor (V75, V75s, Gold, Forex)</p>
    </div>

    <div id="connectionBanner" class="status-banner">
        ⚠️ Backend connection lost. Reconnecting...
    </div>

    <div class="grid">
        <div class="card" id="card-V75">
            <div class="card-header">
                <span class="asset-title">V75</span>
                <span class="feed-badge" id="badge-V75">WAITING FEED...</span>
            </div>
            <div class="price" id="price-V75">---</div>
            <div class="sub-text" id="status-V75">Fetching live ticks...</div>
            <div class="signal-pill" id="signal-V75">INITIALIZING</div>
        </div>

        <div class="card" id="card-V751S">
            <div class="card-header">
                <span class="asset-title">V75 (1s)</span>
                <span class="feed-badge" id="badge-V751S">WAITING FEED...</span>
            </div>
            <div class="price" id="price-V751S">---</div>
            <div class="sub-text" id="status-V751S">Fetching live ticks...</div>
            <div class="signal-pill" id="signal-V751S">INITIALIZING</div>
        </div>

        <div class="card" id="card-XAUUSD">
            <div class="card-header">
                <span class="asset-title">GOLD</span>
                <span class="feed-badge" id="badge-XAUUSD">WAITING FEED...</span>
            </div>
            <div class="price" id="price-XAUUSD">---</div>
            <div class="sub-text" id="status-XAUUSD">Fetching live ticks...</div>
            <div class="signal-pill" id="signal-XAUUSD">INITIALIZING</div>
        </div>

        <div class="card" id="card-GBPUSD">
            <div class="card-header">
                <span class="asset-title">GBP/USD</span>
                <span class="feed-badge" id="badge-GBPUSD">WAITING FEED...</span>
            </div>
            <div class="price" id="price-GBPUSD">---</div>
            <div class="sub-text" id="status-GBPUSD">Fetching live ticks...</div>
            <div class="signal-pill" id="signal-GBPUSD">INITIALIZING</div>
        </div>
    </div>

    <script>
        const banner = document.getElementById('connectionBanner');

        function connectWebSocket() {
            // Automatically switch between ws:// and wss:// based on standard or HTTPS browser URLs
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws`;

            const socket = new WebSocket(wsUrl);

            socket.onopen = () => {
                banner.style.display = 'none';
                console.log('Connected to BTMM WebSocket Engine');
            };

            socket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.symbol) {
                        const sym = data.symbol;
                        const priceEl = document.getElementById(`price-${sym}`);
                        const badgeEl = document.getElementById(`badge-${sym}`);
                        const statusEl = document.getElementById(`status-${sym}`);
                        const signalEl = document.getElementById(`signal-${sym}`);

                        if (priceEl && data.price) {
                            priceEl.innerText = parseFloat(data.price).toFixed(2);
                        }
                        if (badgeEl) {
                            badgeEl.innerText = 'LIVE FEED';
                            badgeEl.classList.add('live');
                        }
                        if (statusEl && data.status) {
                            statusEl.innerText = data.status;
                        }
                        if (signalEl && data.signal) {
                            signalEl.innerText = data.signal;
                        }
                    }
                } catch (err) {
                    console.error('Error processing tick packet:', err);
                }
            };

            socket.onclose = () => {
                banner.style.display = 'block';
                setTimeout(connectWebSocket, 3000);
            };

            socket.onerror = (err) => {
                socket.close();
            };
        }

        connectWebSocket();
    </script>
</body>
</html>
"""

# Connection Manager for active browser WebSocket sessions
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except Exception:
                pass

manager = ConnectionManager()

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    return HTML_TEMPLATE

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive and yield control to loop
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

# Background tick broadcast simulation / Deriv bridge listener
async def stream_btmm_ticks():
    symbols = ["V75", "V751S", "XAUUSD", "GBPUSD"]
    base_prices = {"V75": 450000.0, "V751S": 950000.0, "XAUUSD": 2650.0, "GBPUSD": 1.3150}
    
    import random
    while True:
        await asyncio.sleep(1.5)
        for sym in symbols:
            # Generate tick delta
            variation = (random.random() - 0.5) * (base_prices[sym] * 0.001)
            base_prices[sym] += variation
            
            packet = {
                "symbol": sym,
                "price": base_prices[sym],
                "status": "Scanning BTMM Patterns (M15)",
                "signal": "MONITORING"
            }
            await manager.broadcast(packet)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(stream_btmm_ticks())

if __name__ == "__main__":
    # Explicitly bind to port 8000 to match Railway Public Networking domain target
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

