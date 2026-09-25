import os
import time
import json
import threading
from html import escape

from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

API_KEY = os.environ.get("API_KEY", "CHANGE_THIS_SECRET_KEY")

# KEEP YOUR WEBHOOK ENVIRONMENT VARIABLE THE SAME
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "60"))
DISCORD_UPDATE_INTERVAL = int(os.environ.get("DISCORD_UPDATE_INTERVAL", "10"))

ACCOUNTS_FILE = "accounts.json"
MESSAGE_FILE = "discord_message.json"

accounts = {}
state_lock = threading.Lock()
discord_message_id = None


# ============================================================
# SAVE / LOAD
# ============================================================

def load_accounts():
    global accounts

    if not os.path.exists(ACCOUNTS_FILE):
        print("📁 No previous accounts file found")
        return

    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict):
            accounts = data
            print(f"📂 Loaded {len(accounts)} saved account(s)")

    except Exception as error:
        print("❌ Could not load accounts:")
        print(repr(error))


def save_accounts():
    try:
        with state_lock:
            data = accounts.copy()

        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)

    except Exception as error:
        print("❌ Could not save accounts:")
        print(repr(error))


def load_discord_message():
    global discord_message_id

    if not os.path.exists(MESSAGE_FILE):
        return

    try:
        with open(MESSAGE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        discord_message_id = data.get("message_id")

    except Exception as error:
        print("❌ Could not load Discord message ID:")
        print(repr(error))


def save_discord_message():
    try:
        with open(MESSAGE_FILE, "w", encoding="utf-8") as file:
            json.dump({"message_id": discord_message_id}, file)

    except Exception as error:
        print("❌ Could not save Discord message ID:")
        print(repr(error))


# ============================================================
# HELPERS
# ============================================================

def check_key(req):
    return req.headers.get("X-API-Key") == API_KEY


def account_is_online(account):
    last_seen = float(account.get("lastSeen", 0))
    return (time.time() - last_seen) <= HEARTBEAT_TIMEOUT


def format_money(value):
    if value is None or value == "":
        return "—"

    try:
        number = float(value)

        suffixes = [
            (1e18, "Qi"),
            (1e15, "Qa"),
            (1e12, "T"),
            (1e9, "B"),
            (1e6, "M"),
            (1e3, "K"),
        ]

        for divisor, suffix in suffixes:
            if abs(number) >= divisor:
                return f"${number / divisor:.2f}{suffix}"

        return f"${number:,.0f}"

    except (TypeError, ValueError):
        return escape(str(value))


def format_rate(value):
    if value is None or value == "":
        return "—"

    return escape(str(value))


def format_age(seconds):
    try:
        seconds = max(0, int(seconds))
    except (TypeError, ValueError):
        return "—"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if hours:
        return f"{hours}h {minutes:02d}m"

    return f"{minutes}m"


def get_accounts_snapshot():
    with state_lock:
        return [dict(account) for account in accounts.values()]


def dashboard_stats():
    items = get_accounts_snapshot()

    online = sum(1 for account in items if account_is_online(account))
    offline = len(items) - online

    return online, offline, len(items)


# ============================================================
# DISCORD
# ============================================================

def dashboard_text():
    items = get_accounts_snapshot()

    lines = []
    online = 0
    offline = 0

    for account in sorted(
        items,
        key=lambda x: (x.get("playerName") or "").lower()
    ):
        is_online = account_is_online(account)

        if is_online:
            online += 1
            icon = "🟢 ONLINE"
        else:
            offline += 1
            icon = "🔴 OFFLINE"

        name = account.get("playerName") or account.get("userId")
        eggs = int(account.get("eggs", 0))

        lines.append(
            f"{icon} **{name}** — 🥚 **{eggs:,}**"
        )

    if not lines:
        body = "No accounts have sent a heartbeat yet."
    else:
        body = "\n".join(lines)

    if len(body) > 3900:
        body = body[:3860] + "\n…more accounts not shown"

    return body, online, offline, len(items)


def discord_payload():
    body, online, offline, total = dashboard_text()

    return {
        "embeds": [{
            "title": "🥚 KYOSH ACCOUNT MONITOR",
            "description": body,
            "color": 5763719 if offline == 0 else 15158332,
            "footer": {
                "text": (
                    f"Online: {online} | "
                    f"Offline: {offline} | "
                    f"Total: {total}"
                )
            },
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime()
            )
        }]
    }


def update_discord():
    global discord_message_id

    if not DISCORD_WEBHOOK_URL:
        return

    payload = discord_payload()

    try:
        if discord_message_id:
            url = (
                f"{DISCORD_WEBHOOK_URL}"
                f"/messages/{discord_message_id}"
            )

            response = requests.patch(
                url,
                json=payload,
                timeout=15
            )

            if 200 <= response.status_code < 300:
                return

            if response.status_code == 404:
                discord_message_id = None
                save_discord_message()
            else:
                print("❌ Discord PATCH error:", response.text[:1000])
                return

        url = DISCORD_WEBHOOK_URL + "?wait=true"

        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if 200 <= response.status_code < 300:
            data = response.json()
            discord_message_id = data.get("id")
            save_discord_message()
            print("✅ Discord monitor message created/updated")
        else:
            print("❌ Discord POST error:", response.text[:1000])

    except Exception as error:
        print("❌ Discord connection error:")
        print(repr(error))


def monitor_loop():
    print("🚀 Discord monitor loop started")

    while True:
        try:
            update_discord()
        except Exception as error:
            print("❌ Monitor loop error:", repr(error))

        time.sleep(DISCORD_UPDATE_INTERVAL)


# ============================================================
# WEBSITE
# ============================================================

@app.get("/")
def home():
    return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kyosh Account Monitor</title>

<style>
:root{
    --bg:#08090c;
    --panel:#101216;
    --panel2:#14161b;
    --line:#242730;
    --text:#f4f5f7;
    --muted:#858a96;
    --green:#24d36b;
    --orange:#f2a33a;
    --red:#ff5252;
}

*{box-sizing:border-box}

body{
    margin:0;
    min-height:100vh;
    background:
        radial-gradient(circle at top right, rgba(40,45,65,.28), transparent 35%),
        var(--bg);
    color:var(--text);
    font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
}

.wrapper{
    width:min(1450px,94%);
    margin:0 auto;
    padding:30px 0 45px;
}

.header{
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:20px;
    margin-bottom:24px;
}

.brand{
    display:flex;
    align-items:center;
    gap:13px;
}

.logo{
    width:42px;
    height:42px;
    border-radius:12px;
    display:grid;
    place-items:center;
    background:#171923;
    border:1px solid #2a2d38;
    font-size:20px;
}

h1{
    margin:0;
    font-size:22px;
    letter-spacing:-.5px;
}

.subtitle{
    color:var(--muted);
    font-size:13px;
    margin-top:3px;
}

.live{
    display:flex;
    align-items:center;
    gap:8px;
    padding:9px 13px;
    border:1px solid #20252d;
    background:#101318;
    border-radius:999px;
    color:#aeb4be;
    font-size:12px;
}

.live-dot{
    width:8px;
    height:8px;
    border-radius:50%;
    background:var(--green);
    box-shadow:0 0 10px rgba(36,211,107,.65);
}

.stats{
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:12px;
    margin-bottom:18px;
}

.stat{
    background:var(--panel);
    border:1px solid var(--line);
    border-radius:14px;
    padding:17px 19px;
}

.stat-label{
    color:var(--muted);
    font-size:12px;
    margin-bottom:7px;
}

.stat-value{
    font-size:25px;
    font-weight:750;
}

.table{
    border:1px solid var(--line);
    background:var(--panel);
    border-radius:16px;
    overflow:hidden;
    box-shadow:0 18px 50px rgba(0,0,0,.2);
}

.table-head,
.account{
    display:grid;
    grid-template-columns:minmax(210px,2fr) 105px 135px 135px 70px 70px minmax(190px,1.5fr) 110px;
    align-items:center;
    min-width:900px;
}

.table-head{
    height:48px;
    padding:0 18px;
    color:#707581;
    background:#0d0f13;
    border-bottom:1px solid var(--line);
    font-size:11px;
    font-weight:700;
    text-transform:uppercase;
    letter-spacing:.7px;
}

.account{
    padding:14px 18px;
    min-height:76px;
    border-bottom:1px solid #1e2128;
    transition:.15s ease;
}

.account:last-child{
    border-bottom:0;
}

.account:hover{
    background:#15181e;
}

.user{
    display:flex;
    align-items:center;
    gap:11px;
    min-width:0;
}

.avatar{
    width:35px;
    height:35px;
    flex:0 0 35px;
    border-radius:10px;
    display:grid;
    place-items:center;
    background:#1b1d25;
    border:1px solid #292d38;
    color:#8f96a5;
    font-size:12px;
    font-weight:800;
}

.username{
    overflow:hidden;
    text-overflow:ellipsis;
    white-space:nowrap;
    font-weight:650;
    font-size:14px;
}

.userid{
    color:#646a76;
    font-size:10px;
    margin-top:2px;
}

.status{
    display:inline-flex;
    align-items:center;
    gap:7px;
    font-size:12px;
    font-weight:600;
}

.status-dot{
    width:7px;
    height:7px;
    border-radius:50%;
}

.online .status-dot{
    background:var(--green);
    box-shadow:0 0 8px rgba(36,211,107,.55);
}

.online .status{
    color:#b4b9c1;
}

.offline .status-dot{
    background:var(--red);
}

.offline .status{
    color:#777c86;
}

.money{
    font-weight:700;
    color:#e6e8ec;
}

.rate{
    color:var(--orange);
    font-weight:700;
}

.number{
    color:#d9dce2;
    font-weight:650;
}

.age{
    color:#737985;
    font-size:12px;
}

.multiplier{
    color:#00ff00;
    font-weight:800;
    margin-left:5px;
}


.inventory-cell{
    min-width:0;
}

.inventory-btn{
    width:100%;
    border:1px solid #292d38;
    background:#15181e;
    color:#dfe2e8;
    border-radius:9px;
    padding:7px 9px;
    text-align:left;
    cursor:pointer;
    font:inherit;
}

.inventory-btn:hover{
    background:#1b1f27;
    border-color:#3a3f4c;
}

.inventory-count{
    font-size:12px;
    font-weight:750;
}

.inventory-preview{
    margin-top:3px;
    color:#777e8c;
    font-size:10px;
    overflow:hidden;
    text-overflow:ellipsis;
    white-space:nowrap;
}

.modal{
    position:fixed;
    inset:0;
    z-index:1000;
    display:none;
    align-items:center;
    justify-content:center;
    padding:20px;
    background:rgba(0,0,0,.72);
    backdrop-filter:blur(6px);
}

.modal.open{
    display:flex;
}

.modal-card{
    width:min(900px,96vw);
    max-height:88vh;
    overflow:hidden;
    background:#101216;
    border:1px solid #2a2e38;
    border-radius:16px;
    box-shadow:0 30px 90px rgba(0,0,0,.55);
}

.modal-head{
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:15px;
    padding:16px 18px;
    border-bottom:1px solid var(--line);
}

.modal-title{
    font-weight:800;
    font-size:16px;
}

.modal-subtitle{
    color:var(--muted);
    font-size:11px;
    margin-top:3px;
}

.modal-close{
    border:1px solid #303541;
    background:#171a21;
    color:#dfe2e8;
    border-radius:9px;
    width:34px;
    height:34px;
    cursor:pointer;
    font-size:18px;
}

.inventory-list{
    max-height:70vh;
    overflow:auto;
    padding:12px;
}

.inventory-section-title{
    color:#8f96a5;
    font-size:11px;
    font-weight:800;
    text-transform:uppercase;
    letter-spacing:.7px;
    padding:8px 6px;
}

.inventory-grid-header{
    display:grid;
    grid-template-columns:minmax(180px,2fr) 100px 130px 130px;
    gap:10px;
    align-items:center;
}

.inventory-item{
    display:grid;
    grid-template-columns:minmax(180px,2fr) 100px 130px 130px;
    gap:10px;
    align-items:center;
    padding:10px 8px;
    border-bottom:1px solid #1e2128;
}

.inventory-item:last-child{
    border-bottom:0;
}

.item-name{
    font-weight:700;
    font-size:12px;
}

.item-meta{
    color:#707785;
    font-size:10px;
    margin-top:2px;
}

.item-value{
    font-size:11px;
    font-weight:700;
}

.item-weight{
    color:#dfe2e8;
}

.item-cash{
    color:#e6e8ec;
}

.item-rate{
    color:var(--orange);
}

@media(max-width:800px){
    .inventory-item{
        grid-template-columns:1fr 1fr;
    }
}

.empty{
    padding:70px 20px;
    text-align:center;
    color:var(--muted);
}

.footer{
    padding:13px 18px;
    color:#5f6570;
    font-size:11px;
    border-top:1px solid var(--line);
    background:#0d0f13;
}

@media(max-width:800px){
    .wrapper{width:96%;padding-top:20px}
    .stats{grid-template-columns:1fr}
    .header{align-items:flex-start}
    .live{display:none}
    .table{overflow-x:auto}
}
</style>
</head>

<body>
<div class="wrapper">

    <div class="header">
        <div class="brand">
            <div class="logo">RE</div>
            <div>
                <h1>KYOSH ACCOUNT MONITOR</h1>
                <div class="subtitle">Live account activity and inventory monitor</div>
            </div>
        </div>

        <div class="live">
            <span class="live-dot"></span>
            LIVE MONITOR
        </div>
    </div>

    <div class="stats">
        <div class="stat">
            <div class="stat-label">ONLINE</div>
            <div class="stat-value" id="online">0</div>
        </div>
        <div class="stat">
            <div class="stat-label">OFFLINE</div>
            <div class="stat-value" id="offline">0</div>
        </div>
        <div class="stat">
            <div class="stat-label">TOTAL ACCOUNTS</div>
            <div class="stat-value" id="total">0</div>
        </div>
    </div>

    <div class="table">
        <div class="table-head">
            <div>ACCOUNT</div>
            <div>STATUS</div>
            <div>MONEY</div>
            <div>SPEED</div>
            <div>EGGS</div>
            <div>PETS</div>
            <div>EGG INVENTORY</div>
            <div>LAST SEEN</div>
        </div>

        <div id="accounts">
            <div class="empty">Loading accounts...</div>
        </div>

        <div class="footer">
            Auto-refreshing every 2 seconds • Kyosh Account Monitor
        </div>
    </div>


    <div class="modal" id="inventoryModal">
        <div class="modal-card" onclick="event.stopPropagation()">
            <div class="modal-head">
                <div>
                    <div class="modal-title" id="modalTitle">Inventory</div>
                    <div class="modal-subtitle" id="modalSubtitle"></div>
                </div>
                <button class="modal-close" onclick="closeInventory()">×</button>
            </div>
            <div class="inventory-list" id="inventoryList"></div>
        </div>
    </div>

</div>

<script>
function esc(value){
    return String(value ?? "")
        .replaceAll("&","&amp;")
        .replaceAll("<","&lt;")
        .replaceAll(">","&gt;")
        .replaceAll('"',"&quot;")
        .replaceAll("'","&#039;");
}

function renderMoney(value){
    if(value === null || value === undefined || value === "") return "—";
    return esc(value);
}

function renderRate(value){
    if(value === null || value === undefined || value === "") {
        return "—";
    }

    const text = String(value);

    // Convert:
    // 2B <font color="#00FF00">(x16)</font>
    // into safe HTML with a green multiplier.
    const match = text.match(
        /^(.*?)\s*<font[^>]*>\s*(\(x\d+\))\s*<\/font>\s*$/i
    );

    if(match){
        const amount = match[1].trim();
        const multiplier = match[2];

        return `
            ${esc(amount)}
            <span class="multiplier">${esc(multiplier)}</span>
        `;
    }

    // Remove unsupported font tags if the format is slightly different.
    const cleaned = text
        .replace(/<font[^>]*>/gi, "")
        .replace(/<\/font>/gi, "");

    return esc(cleaned);
}

const accountCache = {};

function inventoryItems(account){
    const inv = account.inventory || {};

    const pets = Array.isArray(inv.pets) ? inv.pets : [];
    const eggs = Array.isArray(inv.eggs) ? inv.eggs : [];

    return {
        pets,
        eggs,
        ready: inv.ready !== false,
        totalPets: Number(inv.totalPets ?? pets.length),
        totalEggs: Number(inv.totalEggs ?? eggs.length)
    };
}

function formatCash(value){
    if(value === null || value === undefined || value === "") return "—";

    const n = Number(value);

    if(!Number.isFinite(n)) return esc(value);

    const suffixes = [
        [1e18, "Qi"],
        [1e15, "Qa"],
        [1e12, "T"],
        [1e9, "B"],
        [1e6, "M"],
        [1e3, "K"]
    ];

    for(const [divisor, suffix] of suffixes){
        if(Math.abs(n) >= divisor){
            return "$" + (n / divisor).toFixed(2) + suffix;
        }
    }

    return "$" + Math.round(n).toLocaleString();
}

function formatWeight(value){
    if(value === null || value === undefined || value === "") return "—";

    const n = Number(value);

    if(!Number.isFinite(n)) return esc(value);

    return n.toLocaleString(undefined, {
        maximumFractionDigits: 2
    }) + "Kg";
}

function formatRateValue(value){
    if(value === null || value === undefined || value === "") return "—";

    const n = Number(value);

    if(!Number.isFinite(n)) return esc(value);

    return formatCash(n) + "/s";
}

function inventoryPreview(account){
    const {pets, eggs, ready, totalPets, totalEggs} = inventoryItems(account);

    if(!ready && !eggs.length && !pets.length){
        return "Waiting for inventory data…";
    }

    if(!eggs.length && !pets.length){
        return "No inventory items";
    }

    const eggText = `${totalEggs.toLocaleString()} egg${totalEggs === 1 ? "" : "s"}`;
    const petText = `${totalPets.toLocaleString()} pet${totalPets === 1 ? "" : "s"}`;

    const names = [
        ...eggs.slice(0,2).map(x => "🥚 " + (x.name || "Egg")),
        ...pets.slice(0,1).map(x => "🐾 " + (x.name || "Pet"))
    ];

    return `${eggText} • ${petText}` + (
        names.length ? ` • ${names.join(" • ")}` : ""
    );
}

function renderAccounts(data){
    const root = document.getElementById("accounts");

    document.getElementById("online").textContent = data.online;
    document.getElementById("offline").textContent = data.offline;
    document.getElementById("total").textContent = data.total;

    if(!data.accounts.length){
        root.innerHTML = '<div class="empty">No accounts have sent a heartbeat yet.</div>';
        return;
    }

    root.innerHTML = data.accounts.map(account => {
        const online = account.online;
        const initial = esc(
            (account.playerName || account.userId || "?")
            .charAt(0)
            .toUpperCase()
        );

        const {pets, eggs, totalPets, totalEggs} = inventoryItems(account);
        const totalInventory = totalPets + totalEggs;

        accountCache[String(account.userId)] = account;

        return `
        <div class="account">
            <div class="user">
                <div class="avatar">${initial}</div>
                <div>
                    <div class="username">${esc(account.playerName || account.userId)}</div>
                    <div class="userid">ID ${esc(account.userId || "—")}</div>
                </div>
            </div>

            <div class="${online ? "online" : "offline"}">
                <span class="status">
                    <span class="status-dot"></span>
                    ${online ? "Online" : "Offline"}
                </span>
            </div>

            <div class="money">${renderMoney(account.money)}</div>
            <div class="rate">${renderRate(account.rate)}</div>
            <div class="number">${Number(account.eggs || 0).toLocaleString()}</div>
            <div class="number">${account.pets ?? "—"}</div>

            <div class="inventory-cell">
                <button
                    class="inventory-btn"
                    data-userid="${esc(account.userId || "")}"
                    onclick="openInventory(this.dataset.userid)"
                >
                    <div class="inventory-count">
                        🥚 ${totalEggs.toLocaleString()} egg${totalEggs === 1 ? "" : "s"}
                        ${totalPets ? ` • 🐾 ${totalPets.toLocaleString()} pet${totalPets === 1 ? "" : "s"}` : ""}
                    </div>
                    <div class="inventory-preview">
                        ${esc(inventoryPreview(account))}
                    </div>
                </button>
            </div>

            <div class="age">${esc(account.lastSeenText || "—")}</div>
        </div>`;
    }).join("");
}

function openInventory(userId){
    const account = accountCache[String(userId)];

    if(!account) return;

    const {pets, eggs, totalPets, totalEggs} = inventoryItems(account);

    document.getElementById("modalTitle").textContent =
        `${account.playerName || account.userId} Inventory`;

    document.getElementById("modalSubtitle").textContent =
        `${totalEggs} eggs • ${totalPets} pets`;

    let html = "";

    if(eggs.length){
        html += `
            <div class="inventory-section-title inventory-grid-header">
                <span>🥚 Eggs</span>
                <span>WEIGHT</span>
                <span>CASH</span>
                <span>TYPE</span>
            </div>`;

        html += eggs.map(item => `
            <div class="inventory-item">
                <div>
                    <div class="item-name">${esc(item.name || "Egg")}</div>
                    <div class="item-meta">
                        ${esc(item.category || "")}
                        ${item.scale ? " • Scale " + esc(item.scale) : ""}
                    </div>
                </div>
                <div class="item-value item-weight">
                    ${formatWeight(item.weight)}
                </div>
                <div class="item-value item-cash">
                    ${formatCash(item.cash)}
                </div>
                <div class="item-value item-rate">Egg</div>
            </div>
        `).join("");
    }

    if(pets.length){
        html += `
            <div class="inventory-section-title inventory-grid-header">
                <span>🐾 Pets</span>
                <span>WEIGHT</span>
                <span>CASH</span>
                <span>RATE</span>
            </div>`;

        html += pets.map(item => {
            const mutations = Array.isArray(item.mutations)
                ? item.mutations.filter(Boolean).join(", ")
                : "";

            return `
            <div class="inventory-item">
                <div>
                    <div class="item-name">${esc(item.name || "Pet")}</div>
                    <div class="item-meta">
                        ${esc(item.category || "")}
                        ${item.favorite ? " • ⭐ Favorite" : ""}
                        ${item.equipped ? " • Equipped" : ""}
                        ${mutations ? " • " + esc(mutations) : ""}
                    </div>
                </div>
                <div class="item-value item-weight">
                    ${formatWeight(item.weight)}
                </div>
                <div class="item-value item-cash">
                    ${formatCash(item.cash)}
                </div>
                <div class="item-value item-rate">
                    ${formatRateValue(item.rate)}
                </div>
            </div>
            `;
        }).join("");
    }

    if(!html){
        html = '<div class="empty">No inventory items were reported.</div>';
    }

    document.getElementById("inventoryList").innerHTML = html;
    document.getElementById("inventoryModal").classList.add("open");
}

function closeInventory(){
    document.getElementById("inventoryModal").classList.remove("open");
}

document.getElementById("inventoryModal").addEventListener("click", closeInventory);

document.addEventListener("keydown", event => {
    if(event.key === "Escape"){
        closeInventory();
    }
});

async function refresh(){
    try{
        const response = await fetch("/api/accounts", {
            cache:"no-store"
        });

        if(!response.ok) throw new Error("Request failed");

        const data = await response.json();
        renderAccounts(data);
    }catch(error){
        document.getElementById("accounts").innerHTML =
            '<div class="empty">Unable to load account data.</div>';
    }
}

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>"""


# ============================================================
# API FOR LIVE WEBSITE
# ============================================================

@app.get("/api/accounts")
def api_accounts():
    items = get_accounts_snapshot()

    result = []

    for account in sorted(
        items,
        key=lambda x: (x.get("playerName") or "").lower()
    ):
        online = account_is_online(account)

        result.append({
            "userId": account.get("userId", ""),
            "playerName": account.get("playerName", ""),
            "displayName": account.get("displayName", ""),
            "online": online,
            "eggs": int(account.get("eggs", 0)),
            "money": account.get("money"),
            "rate": account.get("rate"),
            "pets": account.get("pets"),
            "inventory": account.get("inventory", {
                "pets": [],
                "eggs": [],
                "totalPets": 0,
                "totalEggs": 0,
                "ready": False
            }),
            "lastSeenText": format_age(
                time.time() - float(account.get("lastSeen", time.time()))
            )
        })

    online = sum(1 for x in result if x["online"])
    offline = len(result) - online

    return jsonify({
        "ok": True,
        "online": online,
        "offline": offline,
        "total": len(result),
        "accounts": result
    })


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():
    online, offline, total = dashboard_stats()

    return jsonify({
        "ok": True,
        "online": online,
        "offline": offline,
        "accounts": total,
        "discord_message_id_exists": discord_message_id is not None
    })


# ============================================================
# HEARTBEAT
# ============================================================

@app.post("/heartbeat")
def heartbeat():
    if not check_key(request):
        print("❌ Unauthorized heartbeat request")
        return jsonify({
            "ok": False,
            "error": "Unauthorized"
        }), 401

    data = request.get_json(silent=True) or {}

    user_id = str(data.get("userId", "")).strip()

    if not user_id:
        return jsonify({
            "ok": False,
            "error": "Missing userId"
        }), 400

    player_name = str(
        data.get("playerName") or user_id
    )

    display_name = str(
        data.get("displayName") or ""
    )

    try:
        eggs = int(data.get("eggs", 0))
    except (TypeError, ValueError):
        eggs = 0

    # Optional fields.
    # Existing scripts can keep sending only eggs.
    # Newer scripts can send money/rate/pets later.
    money = data.get("money")
    rate = data.get("rate")
    pets = data.get("pets")

    print("========== HEARTBEAT DEBUG ==========")
    print("PLAYER:", player_name)
    print("MONEY:", money)
    print("RATE:", rate)
    print("PETS:", pets)
    print("EGGS:", eggs)
    print("=====================================")

    with state_lock:
        old = accounts.get(user_id, {})

        incoming_inventory = data.get("inventory")

        if isinstance(incoming_inventory, dict):
            pet_list = incoming_inventory.get("pets", [])
            egg_list = incoming_inventory.get("eggs", [])

            if not isinstance(pet_list, list):
                pet_list = []
            if not isinstance(egg_list, list):
                egg_list = []

            inventory = {
                "pets": pet_list[:500],
                "eggs": egg_list[:500],
                "totalPets": int(
                    incoming_inventory.get("totalPets", len(pet_list)) or 0
                ),
                "totalEggs": int(
                    incoming_inventory.get("totalEggs", len(egg_list)) or 0
                ),
                "ready": incoming_inventory.get("ready", True) is not False
            }
        else:
            inventory = old.get("inventory", {
                "pets": [],
                "eggs": [],
                "totalPets": 0,
                "totalEggs": 0,
                "ready": False
            })

        accounts[user_id] = {
            "userId": user_id,
            "playerName": player_name,
            "displayName": display_name,
            "eggs": eggs,
            "money": money if money is not None else old.get("money"),
            "rate": rate if rate is not None else old.get("rate"),
            "pets": pets if pets is not None else old.get("pets"),
            "inventory": inventory,
            "lastSeen": time.time()
        }

    save_accounts()

    threading.Thread(
        target=update_discord,
        daemon=True
    ).start()

    return jsonify({
        "ok": True,
        "message": "Heartbeat received"
    })


# ============================================================
# STARTUP
# ============================================================

load_accounts()
load_discord_message()

threading.Thread(
    target=monitor_loop,
    daemon=True
).start()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port
    )
