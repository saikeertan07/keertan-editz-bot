import os
import sys
import time
import threading
from playwright.sync_api import sync_playwright

PROFILE_DIR = os.path.expanduser('~/Library/Application Support/KeertanEditz/browser_profile')
os.makedirs(PROFILE_DIR, exist_ok=True)

uploader_state = {
    "status": "idle", # "idle", "waiting_login", "uploading", "ready_for_review", "error"
    "message": "",
    "step_text": "",
    "video_path": "",
    "reel_id": ""
}

uploader_lock = threading.Lock()

def get_uploader_state():
    with uploader_lock:
        return dict(uploader_state)

def set_state(status, message, step_text="", video_path=None, reel_id=None):
    with uploader_lock:
        uploader_state["status"] = status
        uploader_state["message"] = message
        uploader_state["step_text"] = step_text
        if video_path is not None:
            uploader_state["video_path"] = video_path
        if reel_id is not None:
            uploader_state["reel_id"] = reel_id
    print(f"[IG Publisher] [{status}] {message} ({step_text})")

def upload_to_instagram_worker(video_path, caption, reel_id="", auto_share=True):
    set_state("starting", "Launching official browser...", "Starting", video_path=video_path, reel_id=reel_id)
    
    if not os.path.exists(video_path):
        set_state("error", f"Video file not found: {video_path}")
        return False

    # Choose browser channel
    channel = 'msedge'
    try:
        with sync_playwright() as p:
            try:
                test_b = p.chromium.launch(channel='msedge', headless=True)
                test_b.close()
                channel = 'msedge'
            except Exception:
                channel = 'chrome'
    except Exception:
        channel = 'msedge'

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                channel=channel,
                headless=False,
                ignore_default_args=['--enable-automation'],
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--start-maximized',
                    '--no-sandbox'
                ],
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
            )

            global active_browser_context
            active_browser_context = browser

            page = browser.pages[0] if browser.pages else browser.new_page()
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)
            page.bring_to_front()

            set_state("checking_login", "Opening Instagram and checking session...", "Connecting")
            page.goto('https://www.instagram.com/', wait_until='domcontentloaded')
            time.sleep(2)

            # Check if login is required
            cookies = browser.cookies()
            has_session = any(c['name'] == 'sessionid' for c in cookies)
            has_pwd_field = page.locator('input[type="password"]').count() > 0
            has_login_fields = page.locator('input[name="email"], input[name="username"]').count() > 0

            is_login_page = (not has_session) and (has_pwd_field or has_login_fields or 'login' in page.url)

            if is_login_page:
                if auto_share:
                    # In automated queue mode, don't stall the pipeline for 5 minutes if account is not logged in yet
                    set_state(
                        "waiting_login",
                        "Instagram account not connected yet. 4K Reel saved to Output! Click 'Auto-Publish' once logged in.",
                        "Saved to Output",
                        video_path=video_path,
                        reel_id=reel_id
                    )
                    return False

                # Activate browser window so user sees the login screen if they manually clicked publish
                try:
                    browser_app = "Microsoft Edge" if channel == "msedge" else "Google Chrome"
                    import subprocess
                    subprocess.run(['osascript', '-e', f'tell application "{browser_app}" to activate'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

                set_state(
                    "waiting_login",
                    "Please log in to your Instagram account in the opened browser window. Automation will continue automatically once logged in.",
                    "Login Required"
                )
                
                # Wait for user to log in (up to 90s)
                logged_in = False
                for _ in range(90):
                    time.sleep(1)
                    cur_cookies = browser.cookies()
                    has_session = any(c['name'] == 'sessionid' for c in cur_cookies)
                    has_nav = (
                        page.locator('svg[aria-label="New post"]').count() > 0 or
                        page.locator('svg[aria-label="Create"]').count() > 0 or
                        page.locator('svg[aria-label="Home"]').count() > 0
                    )
                    has_pwd = page.locator('input[type="password"]').count() > 0
                    
                    if has_session or (has_nav and not has_pwd):
                        logged_in = True
                        break

                if not logged_in:
                    set_state("error", "Instagram login timed out. Please try again.")
                    return False
                time.sleep(2)

            # Cache cookies for DM listener
            try:
                cur_cookies = browser.cookies()
                cookie_dict = {c['name']: c['value'] for c in cur_cookies}
                if 'sessionid' in cookie_dict:
                    from dm_listener import save_cached_cookies
                    save_cached_cookies(cookie_dict)
            except Exception:
                pass

            # Dismiss any "Save login info" or "Notifications" popups if they appear
            for _ in range(2):
                try:
                    not_now = page.locator('button:has-text("Not now"), button:has-text("Not Now"), button:has-text("Save info"), button:has-text("Save Info")')
                    if not_now.count() > 0 and not_now.first.is_visible():
                        not_now.first.click()
                        time.sleep(1)
                except Exception:
                    pass

            set_state("opening_create", "Opening Instagram Post Creator...", "Navigating")
            
            # Find and open Create modal
            # Try clicking sidebar Create (+) first if present, otherwise navigate to create/style
            create_btn = page.locator('svg[aria-label="New post"], svg[aria-label="Create"], div[role="button"]:has-text("Create"), a:has-text("Create")')
            if create_btn.count() > 0 and create_btn.first.is_visible():
                create_btn.first.click()
                time.sleep(2)
            else:
                page.goto('https://www.instagram.com/create/style/', wait_until='domcontentloaded')
                time.sleep(3)

            # Wait for file upload input to appear (up to 15 seconds)
            file_input = None
            for _ in range(15):
                fi = page.locator('input[type="file"]')
                if fi.count() > 0:
                    file_input = fi.first
                    break
                # If still not found, try clicking Create button once more
                create_btn = page.locator('svg[aria-label="New post"], svg[aria-label="Create"]')
                if create_btn.count() > 0 and create_btn.first.is_visible():
                    try:
                        create_btn.first.click()
                    except Exception:
                        pass
                time.sleep(1)

            if not file_input:
                set_state("error", "Could not locate Instagram upload input. Make sure your account is logged in.")
                return

            set_state("attaching_video", "Attaching 4K video file into Instagram...", "Uploading Video")
            file_input.set_input_files(video_path)

            # Wait for video to process in Instagram editor
            time.sleep(4)

            # Dismiss any "Reels are now shared with everyone" prompt if present
            try:
                ok_btn = page.locator('button:has-text("OK")')
                if ok_btn.count() > 0 and ok_btn.first.is_visible():
                    ok_btn.first.click()
                    time.sleep(1)
            except Exception:
                pass

            # Ensure 9:16 vertical Reels aspect ratio (prevents Instagram from square-cropping)
            set_state("adjusting_crop", "Configuring full 9:16 vertical Reel ratio...", "Formatting")
            try:
                # 1. Open Crop popover
                crop_opened = page.evaluate("""() => {
                    const svg = document.querySelector('svg[aria-label="Select Crop"], svg[aria-label="Select crop"], svg[aria-label*="Crop" i]');
                    if (!svg) return false;
                    const btn = svg.closest('button, [role="button"]') || svg;
                    btn.click();
                    return true;
                }""")
                time.sleep(1)

                if crop_opened:
                    # 2. Click 9:16 (or Original)
                    ratio_clicked = page.evaluate("""() => {
                        const spans = Array.from(document.querySelectorAll('span')).filter(s => 
                            s.textContent && (s.textContent.trim() === '9:16' || s.textContent.trim() === 'Original')
                        );
                        const target = spans.find(s => s.textContent.trim() === '9:16') || spans.find(s => s.textContent.trim() === 'Original');
                        if (!target) return false;
                        
                        let clickable = target;
                        while (clickable && clickable.tagName !== 'DIV' && clickable.tagName !== 'BUTTON') {
                            clickable = clickable.parentElement;
                        }
                        if (clickable) {
                            clickable.click();
                            return true;
                        }
                        target.click();
                        return true;
                    }""")
                    time.sleep(1)
                    print(f"[IG Publisher] Full 9:16 aspect ratio configured: {ratio_clicked}")
            except Exception as e:
                print(f"[IG Publisher] Crop adjustment notice: {e}")

            # Click Next (to cover & trim screen)
            set_state("next_step", "Navigating to caption editor...", "Processing")
            next_btn = page.locator('div[role="button"]:has-text("Next"), button:has-text("Next"), span:has-text("Next")')
            if next_btn.count() > 0:
                next_btn.first.click()
                time.sleep(2)

            # Click Next again (to caption & share screen)
            next_btn = page.locator('div[role="button"]:has-text("Next"), button:has-text("Next"), span:has-text("Next")')
            if next_btn.count() > 0:
                next_btn.first.click()
                time.sleep(2)

            # Fill caption
            if caption:
                set_state("filling_caption", "Inserting extracted caption & hashtags...", "Adding Caption")
                try:
                    caption_box = page.locator('div[aria-label="Write a caption..."], div[role="textbox"]')
                    if caption_box.count() > 0:
                        caption_box.first.click()
                        time.sleep(0.5)
                        try:
                            caption_box.first.fill(caption)
                        except Exception:
                            page.keyboard.insert_text(caption)
                        time.sleep(1)
                except Exception as e:
                    print(f"[IG Publisher] Caption fill notice: {e}")

            # Ensure "Add AI label" is NOT turned on
            try:
                page.evaluate("""() => {
                    const aiSpan = Array.from(document.querySelectorAll("span, div")).find(el => 
                        el.textContent && el.textContent.includes("Add AI label") && el.children.length === 0
                    );
                    if (aiSpan) {
                        let row = aiSpan;
                        while (row && !row.querySelector("input[type=checkbox], [role=switch]")) {
                            row = row.parentElement;
                        }
                        if (row) {
                            const sw = row.querySelector("input[type=checkbox], [role=switch]");
                            if (sw && (sw.getAttribute("aria-checked") === "true" || sw.checked)) {
                                (sw.closest("label") || sw).click();
                            }
                        }
                    }
                }""")
            except Exception as e:
                print(f"[IG Publisher] AI label check notice: {e}")

            # Expand Advanced Settings and toggle "Hide like and view counts" ON
            try:
                set_state("hiding_likes", "Hiding like and view counts in Advanced Settings...", "Settings")
                
                # 1. Expand Advanced Settings
                page.evaluate("""() => {
                    const advSpan = Array.from(document.querySelectorAll("span")).find(s => 
                        s.textContent && s.textContent.trim().toLowerCase() === "advanced settings"
                    );
                    if (advSpan) {
                        const btn = advSpan.closest("[role=button], button") || advSpan.parentElement || advSpan;
                        btn.click();
                    }
                }""")
                time.sleep(1)

                # 2. Toggle "Hide like and view counts on this post" ON
                page.evaluate("""() => {
                    const likeSpan = Array.from(document.querySelectorAll("span, div")).find(el => 
                        el.textContent && el.textContent.includes("Hide like and view counts") && el.children.length === 0
                    );
                    if (likeSpan) {
                        let row = likeSpan;
                        while (row && !row.querySelector("input[type=checkbox], [role=switch]")) {
                            row = row.parentElement;
                        }
                        if (row) {
                            const sw = row.querySelector("input[type=checkbox], [role=switch]");
                            if (sw && sw.getAttribute("aria-checked") !== "true" && !sw.checked) {
                                (sw.closest("label") || sw).click();
                            }
                        }
                    }
                }""")
                time.sleep(0.5)
            except Exception as e:
                print(f"[IG Publisher] Hide likes notice: {e}")

            if auto_share:
                set_state("sharing", "Auto-publishing: Submitting Reel to Instagram...", "Sharing", video_path=video_path, reel_id=reel_id)
                time.sleep(1.5)

                # Attempt to click the Share button in the header
                share_clicked = False
                for attempt in range(5):
                    try:
                        # 1. Use JS evaluate to locate and click 'Share' inside the dialog
                        share_clicked = page.evaluate("""() => {
                            const dialog = document.querySelector('div[role="dialog"]') || document;
                            const elements = Array.from(dialog.querySelectorAll('div[role="button"], button, span, div'));
                            const candidates = elements.filter(el => {
                                const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                                return text === 'share';
                            });
                            for (const el of candidates) {
                                const rect = el.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0) {
                                    const btn = el.closest('[role="button"], button') || el;
                                    btn.click();
                                    return true;
                                }
                            }
                            return false;
                        }""")
                        if share_clicked:
                            print(f"[IG Publisher] Clicked Share button on attempt {attempt+1}")
                            break
                    except Exception as e:
                        print(f"[IG Publisher] Share attempt {attempt+1} error: {e}")
                    time.sleep(1)

                if not share_clicked:
                    # Fallback to direct Playwright locators
                    try:
                        btn = page.locator('div[role="dialog"] div[role="button"]:has-text("Share"), div[role="dialog"] button:has-text("Share"), div[role="button"]:has-text("Share"), button:has-text("Share")').first
                        if btn.is_visible():
                            btn.click()
                            share_clicked = True
                            print("[IG Publisher] Clicked Share button via Playwright locator")
                    except Exception as e:
                        print(f"[IG Publisher] Fallback locator Share click failed: {e}")

                if not share_clicked:
                    set_state("error", "Could not locate Share button in Instagram window.")
                    return False

                set_state("uploading_reel", "Instagram is processing and sharing your 4K reel...", "Publishing", video_path=video_path, reel_id=reel_id)

                # Monitor for completion or confirmation
                start_wait = time.time()
                shared_confirmed = False
                while time.time() - start_wait < 120: # wait up to 2 minutes
                    time.sleep(2)
                    try:
                        # 1. Success dialog text
                        success_loc = page.locator('span:has-text("Your reel has been shared"), span:has-text("Your post has been shared"), div:has-text("Your reel has been shared"), div:has-text("Your post has been shared")')
                        if success_loc.count() > 0 and success_loc.first.is_visible():
                            shared_confirmed = True
                            print("[IG Publisher] Detected 'Your reel has been shared' confirmation!")
                            break

                        # 2. Checkmark or celebration icon
                        checkmark = page.locator('svg[aria-label="Animated checkmark"], img[alt*="check"], svg[aria-label="Checkmark"]')
                        if checkmark.count() > 0 and checkmark.first.is_visible():
                            shared_confirmed = True
                            print("[IG Publisher] Detected animated checkmark!")
                            break

                        # 3. Check for Instagram errors
                        err_loc = page.locator('span:has-text("couldn\'t be shared"), div:has-text("couldn\'t be shared"), span:has-text("Something went wrong"), div:has-text("Something went wrong")')
                        if err_loc.count() > 0 and err_loc.first.is_visible():
                            set_state("error", "Instagram reported: Reel could not be shared.")
                            return False

                        # 4. Check if creation modal is completely gone and back on feed
                        dialog = page.locator('div[role="dialog"]')
                        if dialog.count() == 0 and time.time() - start_wait > 10:
                            shared_confirmed = True
                            print("[IG Publisher] Dialog closed after share, assuming shared.")
                            break
                    except Exception as e:
                        print(f"[IG Publisher] Status check notice: {e}")
                        break

                if shared_confirmed:
                    set_state("shared", "Reel successfully published to Instagram!", "Published", video_path=video_path, reel_id=reel_id)
                    time.sleep(4)
                    return True
                else:
                    set_state("shared", "Reel share submitted successfully!", "Published", video_path=video_path, reel_id=reel_id)
                    time.sleep(3)
                    return True

            else:
                # Bring window to front and activate for user manual review
                page.bring_to_front()
                try:
                    browser_app = "Microsoft Edge" if channel == "msedge" else "Google Chrome"
                    import subprocess
                    subprocess.run(['osascript', '-e', f'tell application "{browser_app}" to activate'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

                set_state(
                    "ready_for_review",
                    "Video and caption loaded successfully! Review your post and click 'Share' in Instagram to publish.",
                    "Ready to Share",
                    video_path=video_path,
                    reel_id=reel_id
                )

                # Keep browser alive until user either shares or closes
                # We wait in background so the user has full access to the window
                for _ in range(300): # 5 minutes
                    time.sleep(2)
                    try:
                        # If user clicked Share, we can detect completion
                        shared_dialog = page.locator('span:has-text("Your reel has been shared"), span:has-text("Your post has been shared")')
                        if shared_dialog.count() > 0 and shared_dialog.first.is_visible():
                            set_state("shared", "Reel successfully published to Instagram!", "Published", video_path=video_path, reel_id=reel_id)
                            time.sleep(5)
                            return True
                    except Exception:
                        break
                return False

    except Exception as e:
        set_state("error", f"Instagram automation error: {str(e)}", "Failed")
        return False
    finally:
        active_browser_context = None
        with uploader_lock:
            if uploader_state.get("status") not in ["shared", "error"]:
                uploader_state["status"] = "idle"

active_browser_context = None

def publish_to_instagram_sync(video_path, caption, reel_id="", auto_share=True):
    return upload_to_instagram_worker(video_path, caption, reel_id=reel_id, auto_share=auto_share)

def start_instagram_upload(video_path, caption, reel_id="", auto_share=True):
    global active_browser_context
    with uploader_lock:
        current_status = uploader_state.get("status", "idle")
        if current_status in ["attaching_video", "filling_caption", "hiding_likes", "starting", "checking_login", "sharing", "uploading_reel"]:
            return False, "An upload is actively automating right now. Please wait a few seconds."
        
        if active_browser_context:
            try:
                active_browser_context.close()
            except Exception:
                pass
            active_browser_context = None

        set_state("starting", "Preparing Instagram uploader...", "Starting", video_path=video_path, reel_id=reel_id)
    th = threading.Thread(target=upload_to_instagram_worker, args=(video_path, caption, reel_id, auto_share), daemon=True)
    th.start()
    return True, "Upload started"

