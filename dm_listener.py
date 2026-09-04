import os
import sys
import json
import time
import re
import threading
import requests
from playwright.sync_api import sync_playwright

PROFILE_DIR = os.path.expanduser('~/Library/Application Support/KeertanEditz/browser_profile')
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROCESSED_FILE = os.path.join(APP_DIR, 'dm_processed_reels.json')

REEL_REGEX = re.compile(r'(?:https?://(?:www\.)?instagram\.com/(?:reel|p)/([A-Za-z0-9_-]+))|/reel/([A-Za-z0-9_-]+)/')

# Global state for DM Listener
dm_state = {
    "enabled": False,
    "status": "idle", # "idle", "listening", "checking", "waiting_login", "error"
    "message": "DM Auto-Ingest is standby",
    "last_checked": "",
    "found_count": 0,
    "recent_activity": []
}

dm_lock = threading.Lock()
listener_thread = None
stop_event = threading.Event()
enqueue_callback_fn = None

def get_dm_state():
    with dm_lock:
        state = dict(dm_state)
        state["recent_activity"] = list(dm_state["recent_activity"][-20:])
        state["processed_reels"] = load_processed_reels()
        return state

def add_activity(msg, level="info"):
    with dm_lock:
        timestamp = time.strftime("%H:%M:%S")
        dm_state["recent_activity"].append({
            "timestamp": timestamp,
            "message": msg,
            "level": level
        })
        if len(dm_state["recent_activity"]) > 50:
            dm_state["recent_activity"].pop(0)
    print(f"[DM Listener] [{level.upper()}] {msg}")

def load_processed_reels():
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_processed_reel(shortcode, url, sender=""):
    reels = load_processed_reels()
    if any(r.get("shortcode") == shortcode for r in reels):
        return
    reels.insert(0, {
        "shortcode": shortcode,
        "url": url,
        "sender": sender,
        "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "enqueued"
    })
    try:
        with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
            json.dump(reels[:200], f, indent=2)
    except Exception as e:
        print(f"[DM Listener] Error saving processed reels: {e}")

def is_reel_processed(shortcode):
    reels = load_processed_reels()
    return any(r.get("shortcode") == shortcode for r in reels)

def extract_reel_from_item(item):
    """
    Extracts reel shortcode & URL from an Instagram DM message item.
    """
    if not isinstance(item, dict):
        return None

    item_type = item.get("item_type", "")

    # 1. Clip (Reel attachment)
    if item_type == "clip" or "clip" in item:
        clip = item.get("clip", {})
        media = clip.get("media", {})
        code = media.get("code")
        if code:
            return code, f"https://www.instagram.com/reel/{code}/"

    # 2. Reel share
    if item_type == "reel_share" or "reel_share" in item:
        reel_share = item.get("reel_share", {})
        media = reel_share.get("media", {})
        code = media.get("code")
        if code:
            return code, f"https://www.instagram.com/reel/{code}/"

    # 3. Media share
    if item_type == "media_share" or "media_share" in item:
        media_share = item.get("media_share", {})
        code = media_share.get("code")
        if code:
            return code, f"https://www.instagram.com/reel/{code}/"

    # 4. Felix share (IGTV / Video)
    if item_type == "felix_share" or "felix_share" in item:
        video = item.get("felix_share", {}).get("video", {})
        code = video.get("code")
        if code:
            return code, f"https://www.instagram.com/reel/{code}/"

    # 5. Link share
    if item_type == "link" or "link" in item:
        link_obj = item.get("link", {})
        link_url = link_obj.get("link_context", {}).get("link_url", "") or link_obj.get("text", "")
        m = REEL_REGEX.search(link_url)
        if m:
            code = m.group(1) or m.group(2)
            return code, f"https://www.instagram.com/reel/{code}/"

    # 6. Text message with URL
    text = item.get("text", "")
    if text:
        m = REEL_REGEX.search(text)
        if m:
            code = m.group(1) or m.group(2)
            return code, f"https://www.instagram.com/reel/{code}/"

    return None

COOKIE_CACHE_FILE = os.path.join(APP_DIR, 'dm_session_cache.json')
memory_cookies = {}
login_browser_active = False

def save_cached_cookies(cookies_dict):
    global memory_cookies
    if not isinstance(cookies_dict, dict) or not cookies_dict:
        return
    memory_cookies.update(cookies_dict)
    try:
        with open(COOKIE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(memory_cookies, f)
    except Exception as e:
        print(f"[DM Listener] Error saving cookie cache: {e}")

def load_cached_cookies():
    global memory_cookies
    if memory_cookies and 'sessionid' in memory_cookies:
        return memory_cookies
    if os.path.exists(COOKIE_CACHE_FILE):
        try:
            with open(COOKIE_CACHE_FILE, "r", encoding="utf-8") as f:
                memory_cookies = json.load(f)
                return memory_cookies
        except Exception:
            pass
    return memory_cookies

def get_session_cookies():
    """
    Extracts cookies with smart caching to avoid locking PROFILE_DIR.
    """
    global memory_cookies
    cached = load_cached_cookies()
    if cached and 'sessionid' in cached:
        return cached

    # Do not launch Playwright if login browser is open
    if login_browser_active:
        return cached or {}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                channel='msedge',
                headless=True,
                ignore_default_args=['--enable-automation'],
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox'],
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
            )
            cookies = browser.cookies()
            browser.close()
            cookie_dict = {c['name']: c['value'] for c in cookies}
            if 'sessionid' in cookie_dict:
                save_cached_cookies(cookie_dict)
            return cookie_dict
    except Exception as e:
        print(f"[DM Listener] Could not read cookies from profile: {e}")
        return cached or {}

def check_inbox_via_api(cookie_dict):
    """
    Queries Instagram direct_v2 API using authenticated session cookies.
    Returns (status, list_of_reels).
    """
    if 'sessionid' not in cookie_dict:
        return "no_session", []

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'X-CSRFToken': cookie_dict.get('csrftoken', ''),
        'X-IG-App-ID': '936619743392459',
        'X-ASBD-ID': '129477',
        'X-IG-WWW-Claim': '0',
        'Referer': 'https://www.instagram.com/direct/inbox/',
        'Accept': '*/*',
    }

    try:
        resp = requests.get('https://www.instagram.com/api/v1/direct_v2/inbox/', headers=headers, cookies=cookie_dict, timeout=15)
        if resp.status_code == 401 or resp.status_code == 403:
            return "login_required", []
        if resp.status_code != 200:
            return f"http_{resp.status_code}", []

        data = resp.json()
        inbox = data.get('inbox', {})
        threads = inbox.get('threads', [])
        found_reels = []

        for thread in threads:
            users = [u.get('username') for u in thread.get('users', [])]
            sender_name = ", ".join(users) if users else "Instagram User"
            items = thread.get('items', [])
            
            # Inspect the latest 5 messages in this thread
            for item in items[:5]:
                extracted = extract_reel_from_item(item)
                if extracted:
                    shortcode, reel_url = extracted
                    found_reels.append({
                        "shortcode": shortcode,
                        "url": reel_url,
                        "sender": sender_name
                    })

        return "ok", found_reels

    except Exception as e:
        print(f"[DM Listener] API inbox error: {e}")
        return f"error_{str(e)}", []

def check_inbox_via_browser():
    """
    Opens browser context and checks direct inbox via DOM if API is unavailable.
    """
    channel = 'msedge'
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                channel=channel,
                headless=False,
                args=['--disable-blink-features=AutomationControlled']
            )
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.goto("https://www.instagram.com/direct/inbox/", wait_until="domcontentloaded")
            time.sleep(3)

            # Check if login required
            if "login" in page.url or page.locator('input[type="password"]').count() > 0:
                browser.close()
                return "login_required", []

            # Check if threads have reel links
            reel_links = page.evaluate("""() => {
                const anchors = Array.from(document.querySelectorAll('a[href*="/reel/"], a[href*="/p/"]'));
                return anchors.map(a => a.href);
            }""")
            
            browser.close()
            found = []
            for link in reel_links:
                m = REEL_REGEX.search(link)
                if m:
                    code = m.group(1) or m.group(2)
                    found.append({
                        "shortcode": code,
                        "url": f"https://www.instagram.com/reel/{code}/",
                        "sender": "Direct Message"
                    })
            return "ok", found
    except Exception as e:
        return f"browser_error_{str(e)}", []

def poll_inbox_cycle():
    """
    Runs one check cycle of the Instagram inbox.
    """
    with dm_lock:
        dm_state["status"] = "checking"
        dm_state["last_checked"] = time.strftime("%H:%M:%S")

    cookies = get_session_cookies()
    status, reels = check_inbox_via_api(cookies)

    if status == "no_session" or status == "login_required":
        prev_status = dm_state.get("status")
        with dm_lock:
            dm_state["status"] = "waiting_login"
            dm_state["message"] = "Login required: Please connect your Instagram account."
        if prev_status != "waiting_login":
            add_activity("Instagram session missing or expired. Click 'Connect Account' to authenticate.", "warning")
        return

    if status != "ok":
        with dm_lock:
            dm_state["status"] = "error"
            dm_state["message"] = f"Inbox check notice: {status}"
        return

    with dm_lock:
        dm_state["status"] = "listening"
        dm_state["message"] = f"Active & Listening (Checked {dm_state['last_checked']})"

    new_count = 0
    for r in reels:
        code = r["shortcode"]
        url = r["url"]
        sender = r["sender"]
        if not is_reel_processed(code):
            new_count += 1
            save_processed_reel(code, url, sender=sender)
            add_activity(f"📥 New Reel received from @{sender}: {url}", "success")
            
            # Enqueue into Studio Pipeline
            if enqueue_callback_fn:
                try:
                    enqueue_callback_fn(url, {"quality": "4k", "auto_publish": True})
                    add_activity(f"⚡ Enqueued {code} for 4K rendering & Auto-Publishing", "success")
                except Exception as eq_err:
                    add_activity(f"Could not enqueue {code}: {eq_err}", "error")

    if new_count > 0:
        with dm_lock:
            dm_state["found_count"] += new_count
        add_activity(f"Successfully processed {new_count} incoming reel(s).", "success")

def listener_worker():
    print("[DM Listener] Worker thread started.")
    add_activity("Instagram DM Auto-Ingest started. Polling every 30s.", "info")
    
    # Run immediate check on start
    poll_inbox_cycle()

    while not stop_event.is_set():
        # Sleep in 1s increments so we can exit quickly if stopped
        for _ in range(30):
            if stop_event.is_set():
                break
            time.sleep(1)

        if not stop_event.is_set():
            try:
                poll_inbox_cycle()
            except Exception as e:
                print(f"[DM Listener] Cycle exception: {e}")
                add_activity(f"Cycle error: {e}", "error")

    with dm_lock:
        dm_state["status"] = "idle"
        dm_state["enabled"] = False
        dm_state["message"] = "DM Auto-Ingest stopped."
    add_activity("Instagram DM Auto-Ingest stopped.", "info")

def start_dm_listener(enqueue_cb=None):
    global listener_thread, enqueue_callback_fn
    if enqueue_cb:
        enqueue_callback_fn = enqueue_cb

    with dm_lock:
        if dm_state["enabled"]:
            return True, "DM listener already running"
        dm_state["enabled"] = True
        dm_state["status"] = "listening"
        dm_state["message"] = "Starting DM Auto-Ingest..."

    stop_event.clear()
    listener_thread = threading.Thread(target=listener_worker, daemon=True)
    listener_thread.start()
    return True, "DM Auto-Ingest started"

def stop_dm_listener():
    with dm_lock:
        dm_state["enabled"] = False
        dm_state["message"] = "Stopping DM Auto-Ingest..."
    stop_event.set()
    return True, "DM Auto-Ingest stopping"

def open_instagram_login_browser():
    """
    Opens an interactive browser window to Instagram Direct so the user can log in without bot detection.
    """
    def _login_thread():
        global login_browser_active
        with dm_lock:
            if login_browser_active:
                print("[DM Listener] Login browser already open.")
                return
            login_browser_active = True
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=PROFILE_DIR,
                    channel='msedge',
                    headless=False,
                    ignore_default_args=['--enable-automation'],
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--start-maximized',
                        '--no-sandbox'
                    ],
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
                )
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                """)
                page.goto("https://www.instagram.com/direct/inbox/", wait_until="domcontentloaded")
                
                # Bring to front
                try:
                    import subprocess
                    subprocess.run(['osascript', '-e', 'tell application "Microsoft Edge" to activate'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

                # Wait for user to log in and keep open for up to 15 minutes
                for _ in range(450):
                    time.sleep(2)
                    try:
                        cookies = browser.cookies()
                        cookie_dict = {c['name']: c['value'] for c in cookies}
                        if 'sessionid' in cookie_dict:
                            save_cached_cookies(cookie_dict)
                            add_activity("✓ Instagram session logged in successfully!", "success")
                            time.sleep(3)
                            break
                    except Exception:
                        break
                try:
                    browser.close()
                except Exception:
                    pass
        except Exception as e:
            err_msg = str(e)
            if "Target closed" not in err_msg and "browser has been closed" not in err_msg:
                add_activity(f"Login notice: {err_msg}", "warning")
        finally:
            with dm_lock:
                login_browser_active = False
            threading.Thread(target=poll_inbox_cycle, daemon=True).start()

    th = threading.Thread(target=_login_thread, daemon=True)
    th.start()
    return True, "Login browser opened"
