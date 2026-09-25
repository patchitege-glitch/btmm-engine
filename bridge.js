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
        process.stdout.write(data.toString() + '\n');
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