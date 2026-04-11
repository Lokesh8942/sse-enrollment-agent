import os
import time
import json
import re
import threading
import requests
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException

load_dotenv()

USERNAME = os.getenv("COLLEGE_USER")
PASSWORD = os.getenv("COLLEGE_PASS")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT  = os.getenv("TG_CHAT")

if not USERNAME or not PASSWORD:
    raise EnvironmentError("COLLEGE_USER and COLLEGE_PASS must be set as environment variables.")

LOGIN_URL  = "https://arms.sse.saveetha.com/Login.aspx"
ENROLL_URL = "https://arms.sse.saveetha.com/StudentPortal/Enrollment.aspx"

DATA_FILE   = "agent_memory.json"   # persists seen courses
STATS_FILE  = "agent_stats.json"    # persists daily alert count

EXCLUDED_SLOTS = {"E", "5"}

# ── Shared state ──────────────────────────────────────────
dashboard_state = {
    "last_updated": "Never",
    "courses": {},
    "log": []
}
state_lock = threading.Lock()

stats = {
    "last_check":      "Never",
    "session_alive":   False,
    "login_failures":  0,
    "alerts_today":    0,
    "alerts_date":     datetime.now().strftime("%Y-%m-%d"),
}


# ── Stats helpers ─────────────────────────────────────────

def load_stats():
    global stats
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE) as f:
                saved = json.load(f)
            today = datetime.now().strftime("%Y-%m-%d")
            # Reset daily count if it's a new day
            if saved.get("alerts_date") != today:
                saved["alerts_today"] = 0
                saved["alerts_date"]  = today
            stats.update(saved)
        except Exception:
            pass

def save_stats():
    with open(STATS_FILE, "w") as f:
        json.dump({
            "last_check":     stats["last_check"],
            "login_failures": stats["login_failures"],
            "alerts_today":   stats["alerts_today"],
            "alerts_date":    stats["alerts_date"],
        }, f)

def increment_alerts():
    today = datetime.now().strftime("%Y-%m-%d")
    if stats["alerts_date"] != today:
        stats["alerts_today"] = 0
        stats["alerts_date"]  = today
    stats["alerts_today"] += 1
    save_stats()


# ── Dashboard HTML ────────────────────────────────────────

def render_dashboard():
    with state_lock:
        courses      = dashboard_state["courses"]
        last_updated = dashboard_state["last_updated"]
        log          = dashboard_state["log"][-20:]

    rows = ""
    for code, info in sorted(courses.items()):
        seats   = info.get("seats", 0)
        title   = info.get("title", "-")
        faculty = info.get("faculty", "-")
        slot    = info.get("slot", "-")
        t       = info.get("time", "-")
        is_new  = info.get("is_new", False)

        badge = (f'<span class="badge open">{seats} seats</span>'
                 if seats > 0 else '<span class="badge full">Full</span>')
        new_tag = '<span class="new-tag">NEW</span>' if is_new else ""

        rows += f"""
        <tr>
          <td>{slot}</td>
          <td><strong>{code}</strong> {new_tag}</td>
          <td>{title}</td>
          <td>{faculty}</td>
          <td>{badge}</td>
          <td>{t}</td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="6" class="empty">No courses scanned yet.</td></tr>'

    log_lines = "".join(f"<div class='log-line'>{l}</div>" for l in reversed(log))

    alive_dot  = "🟢" if stats["session_alive"] else "🔴"
    stat_block = f"""
      <div class="stat-grid">
        <div class="stat">{alive_dot} Session alive: {"Yes" if stats["session_alive"] else "No"}</div>
        <div class="stat">🕒 Last check: {stats["last_check"]}</div>
        <div class="stat">⚠️ Login failures: {stats["login_failures"]}</div>
        <div class="stat">📬 Alerts today: {stats["alerts_today"]}</div>
        <div class="stat">🖥️ Server time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>
      </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="60">
  <title>SSE Enrollment Monitor</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;padding:2rem}}
    h1{{font-size:1.8rem;font-weight:700;color:#38bdf8;margin-bottom:.3rem}}
    .subtitle{{color:#94a3b8;font-size:.9rem;margin-bottom:1.5rem}}
    .stat-grid{{display:flex;flex-wrap:wrap;gap:.75rem;margin-bottom:1.5rem}}
    .stat{{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:.5rem 1rem;font-size:.82rem;color:#cbd5e1}}
    .last-updated{{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:.5rem 1rem;display:inline-block;font-size:.82rem;color:#94a3b8;margin-bottom:1.2rem}}
    .table-wrap{{overflow-x:auto}}
    table{{width:100%;border-collapse:collapse;background:#1e293b;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.4)}}
    thead{{background:#0f172a}}
    th{{padding:.9rem 1rem;text-align:left;font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;color:#64748b}}
    td{{padding:.8rem 1rem;border-top:1px solid #334155;font-size:.88rem}}
    tr:hover td{{background:#263348}}
    .badge{{padding:.3rem .75rem;border-radius:999px;font-size:.78rem;font-weight:600}}
    .badge.open{{background:#064e3b;color:#34d399;border:1px solid #34d399}}
    .badge.full{{background:#3b0764;color:#c084fc;border:1px solid #c084fc}}
    .new-tag{{background:#1e3a5f;color:#60a5fa;border:1px solid #3b82f6;border-radius:4px;font-size:.7rem;padding:.1rem .4rem;margin-left:.4rem;font-weight:600}}
    .empty{{text-align:center;color:#475569;padding:2rem}}
    .log-section{{margin-top:2rem}}
    .log-section h2{{font-size:.85rem;color:#64748b;margin-bottom:.8rem;text-transform:uppercase;letter-spacing:.05em}}
    .log-box{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:1rem 1.2rem;font-family:monospace;font-size:.78rem;color:#94a3b8;max-height:220px;overflow-y:auto}}
    .log-line{{padding:2px 0;border-bottom:1px solid #263348}}
    .footer{{margin-top:2rem;font-size:.75rem;color:#334155}}
  </style>
</head>
<body>
  <h1>📋 SSE Enrollment Monitor</h1>
  <p class="subtitle">Auto-refreshes every 60s · Scans every 5 min · Slots E &amp; 5 excluded</p>
  {stat_block}
  <div class="last-updated">🕒 Last scanned: {last_updated}</div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>Slot</th><th>Code</th><th>Title</th><th>Faculty</th><th>Availability</th><th>Detected At</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <div class="log-section">
    <h2>Agent Log</h2>
    <div class="log-box">{log_lines or "<div class='log-line'>No logs yet.</div>"}</div>
  </div>
  <p class="footer">Saveetha School of Engineering · ARMS Portal Monitor</p>
</body>
</html>"""


# ── Web server ────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        html = render_dashboard().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, *args):
        pass

def start_dashboard_server():
    port = int(os.getenv("PORT", 8080))
    HTTPServer(("0.0.0.0", port), DashboardHandler).serve_forever()


# ── Logging ───────────────────────────────────────────────

def add_log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with state_lock:
        dashboard_state["log"].append(line)


# ── Telegram helpers ──────────────────────────────────────

def tg_send(text):
    if not TG_TOKEN or not TG_CHAT:
        add_log(f"(Telegram not configured) {text}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": text},
            timeout=10
        )
    except Exception as e:
        add_log(f"Telegram error: {e}")


def tg_get_updates(offset):
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35
        )
        return r.json().get("result", [])
    except Exception:
        return []


def handle_status_command():
    alive_str = "Yes ✅" if stats["session_alive"] else "No ❌"
    msg = (
        f"Bot Status 🟢\n"
        f"{'─'*30}\n"
        f"🕒 Last check: {stats['last_check']}\n"
        f"🔑 Session alive: {alive_str}\n"
        f"⚠️ Login failures: {stats['login_failures']}\n"
        f"📬 Alerts today: {stats['alerts_today']}\n"
        f"🖥️ Server time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    tg_send(msg)


def poll_telegram_commands():
    """Listen for /status command from Telegram."""
    if not TG_TOKEN:
        return
    offset = 0
    add_log("Telegram command polling started.")
    while True:
        try:
            updates = tg_get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "").strip().lower()
                if text.startswith("/status"):
                    add_log("Received /status command.")
                    handle_status_command()
        except Exception as e:
            add_log(f"Poll error: {e}")
        time.sleep(2)


# ── Agent ─────────────────────────────────────────────────

class EnrollmentAgent:

    def __init__(self):
        self.memory = self.load_memory()

    def load_memory(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"seen_courses": {}}

    def save_memory(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.memory, f, indent=2)

    def build_driver(self):
        opts = Options()
        opts.binary_location = "/usr/bin/google-chrome"
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        return webdriver.Chrome(options=opts)

    def observe(self):
        add_log("Launching browser...")
        driver = self.build_driver()
        data   = {}

        try:
            driver.get(LOGIN_URL)
            time.sleep(5)

            try:
                uf = driver.find_element(By.ID, "txtusername")
            except NoSuchElementException:
                stats["login_failures"] += 1
                save_stats()
                raise Exception("Login page not found.")

            uf.send_keys(USERNAME)
            driver.find_element(By.ID, "txtpassword").send_keys(PASSWORD)
            driver.find_element(By.ID, "btnlogin").click()
            time.sleep(5)

            if "Login" in driver.current_url:
                stats["login_failures"] += 1
                stats["session_alive"]   = False
                save_stats()
                raise Exception("Login failed — still on login page.")

            stats["session_alive"] = True
            add_log(f"Logged in. URL: {driver.current_url}")

            driver.get(ENROLL_URL)
            time.sleep(5)

            sel   = Select(driver.find_element(By.ID, "cphbody_ddlslot"))
            slots = [o.get_attribute("value") for o in sel.options if o.get_attribute("value")]
            add_log(f"Found {len(slots)} slots.")

            for slot in slots:
                if slot.strip().upper() in EXCLUDED_SLOTS:
                    continue

                sel.select_by_value(slot)
                time.sleep(3)
                slot_name = sel.first_selected_option.text.strip()

                for row in driver.find_elements(By.TAG_NAME, "tr"):
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) < 3:
                        continue
                    text = row.text.strip()
                    if not text:
                        continue

                    code = None
                    for part in text.split():
                        if re.match(r"^[A-Z]{2,}\d{3,}", part):
                            code = part
                            break
                    if not code:
                        continue

                    ct     = [c.text.strip() for c in cells]
                    title  = ct[2] if len(ct) > 2 else ""
                    faculty= ct[3] if len(ct) > 3 else ""
                    nums   = re.findall(r"\d+", text)
                    seats  = int(nums[-1]) if nums else 0

                    entry = {
                        "seats":   seats,
                        "title":   title,
                        "faculty": faculty,
                        "slot":    slot_name,
                        "time":    time.strftime("%H:%M:%S"),
                        "is_new":  False,
                    }
                    if code not in data or seats > data[code]["seats"]:
                        data[code] = entry

            add_log(f"Scan complete. {len(data)} courses found.")
            return data

        finally:
            driver.quit()

    def check_and_notify(self, current_data):
        seen = self.memory.get("seen_courses", {})

        new_courses    = []
        opened_courses = []

        for code, info in current_data.items():
            if code not in seen:
                # Brand new course never seen before
                info["is_new"] = True
                new_courses.append((code, info))
            else:
                info["is_new"] = False
                # Was full before, now has seats
                if seen[code].get("seats", 0) == 0 and info["seats"] > 0:
                    opened_courses.append((code, info))

        # ── New course alerts ──
        slot_summary = {}
        for code, info in new_courses:
            slot = info.get("slot", "Unknown")
            msg  = (
                f"🆕 NEW COURSE DETECTED\n"
                f"{'─'*30}\n"
                f"📍 Slot: {slot}\n"
                f"📚 Code: {code}\n"
                f"📖 Title: {info.get('title','N/A')}\n"
                f"👩‍🏫 Faculty: {info.get('faculty','N/A')}\n"
                f"🪑 Vacancies: {info.get('seats',0)}\n"
                f"🕐 Time: {info.get('time','N/A')}"
            )
            add_log(f"NEW: {code} | {slot} | {info.get('title','')}")
            tg_send(msg)
            increment_alerts()
            slot_summary[slot] = slot_summary.get(slot, 0) + 1

        if slot_summary:
            lines   = "\n".join(f"{s}: {c}" for s, c in slot_summary.items())
            total   = sum(slot_summary.values())
            tg_send(f"📊 Cycle summary: {total} new course(s)\n{lines}")

        # ── Seats opened alerts ──
        for code, info in opened_courses:
            slot = info.get("slot", "Unknown")
            msg  = (
                f"🟢 SEATS NOW AVAILABLE\n"
                f"{'─'*30}\n"
                f"📍 Slot: {slot}\n"
                f"📚 Code: {code}\n"
                f"📖 Title: {info.get('title','N/A')}\n"
                f"👩‍🏫 Faculty: {info.get('faculty','N/A')}\n"
                f"🪑 Vacancies: {info.get('seats',0)}\n"
                f"🕐 Time: {info.get('time','N/A')}"
            )
            add_log(f"Seats opened: {code} | {slot}")
            tg_send(msg)
            increment_alerts()

        if not new_courses and not opened_courses:
            add_log("No new courses or seat changes.")

        # Save seen_courses — only add new ones, never remove
        # This ensures courses already seen are never re-alerted after restart
        merged = {**seen, **current_data}
        self.memory["seen_courses"] = merged
        self.save_memory()

        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
        stats["last_check"] = now
        save_stats()

        with state_lock:
            dashboard_state["courses"]      = current_data
            dashboard_state["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def run_loop(self):
        add_log("Agent started. Scanning every 5 minutes.")
        while True:
            try:
                add_log("Starting scan...")
                current_data = self.observe()
                self.check_and_notify(current_data)
            except Exception as e:
                add_log(f"Error: {e}")
            time.sleep(300)


# ── Entry point ───────────────────────────────────────────

if __name__ == "__main__":
    load_stats()

    # Dashboard web server
    threading.Thread(target=start_dashboard_server, daemon=True).start()

    # Telegram /status command listener
    threading.Thread(target=poll_telegram_commands, daemon=True).start()

    EnrollmentAgent().run_loop()
