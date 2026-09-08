import os
import sys
import json
import time
import shutil
import subprocess
import threading
import urllib.parse
from flask import Flask, request, jsonify, render_template_string, send_file
from pipeline import run_pipeline, OUTPUT_DIR, APP_DIR
from instagram_publisher import start_instagram_upload, get_uploader_state, publish_to_instagram_sync
from dm_listener import (
    get_dm_state,
    start_dm_listener,
    stop_dm_listener,
    poll_inbox_cycle,
    open_instagram_login_browser
)

app = Flask(__name__)

# Queue management
task_queue = []

def enqueue_from_dm(url, options):
    with queue_lock:
        task_queue.append((url, options))
active_task = {
    "status": "idle", # "idle", "running", "done", "error"
    "message": "",
    "step": 0,
    "total": 5,
    "current_url": "",
    "queue_remaining": 0,
    "result": None,
    "error": None
}

queue_lock = threading.Lock()

def load_completed_reels_from_disk():
    reels = []
    if not os.path.exists(OUTPUT_DIR):
        return reels
    try:
        for entry in sorted(os.listdir(OUTPUT_DIR), reverse=True):
            folder_path = os.path.join(OUTPUT_DIR, entry)
            if os.path.isdir(folder_path) and entry.startswith("Reel_"):
                reel_id = entry.replace("Reel_", "")
                vid_path, thumb_path, desc_path, desc_content = None, None, None, ""
                quality, size_mb = "4k", 0
                is_published = False
                published_at = ""
                pub_file = os.path.join(folder_path, "published.json")
                if os.path.exists(pub_file):
                    try:
                        with open(pub_file, "r", encoding="utf-8") as pf:
                            pdata = json.load(pf)
                            is_published = pdata.get("published", True)
                            published_at = pdata.get("published_at", "")
                    except Exception:
                        is_published = True

                for f in os.listdir(folder_path):
                    fp = os.path.join(folder_path, f)
                    if f.endswith(".mp4"):
                        vid_path = fp
                        quality = "1080p" if "1080p" in f else "4k"
                        try:
                            size_mb = round(os.path.getsize(fp)/(1024*1024), 1)
                        except Exception:
                            pass
                    elif f.endswith(".jpg") and "thumb" in f:
                        thumb_path = fp
                    elif f.endswith(".txt") and "desc" in f:
                        desc_path = fp
                        try:
                            with open(fp, "r", encoding="utf-8") as tf:
                                desc_content = tf.read().strip()
                        except Exception:
                            pass
                if vid_path:
                    reels.append({
                        "reel_id": reel_id,
                        "quality": quality,
                        "reel_folder": folder_path,
                        "folder_name": entry,
                        "video_path": vid_path,
                        "thumbnail_path": thumb_path or "",
                        "description_path": desc_path or "",
                        "description": desc_content,
                        "video_name": os.path.basename(vid_path),
                        "thumbnail_name": os.path.basename(thumb_path) if thumb_path else "",
                        "description_name": os.path.basename(desc_path) if desc_path else "",
                        "file_size_mb": size_mb,
                        "is_published": is_published,
                        "published_at": published_at,
                        "instagram_create_url": "https://www.instagram.com/create/style/"
                    })
    except Exception as e:
        print(f"Error loading completed reels: {e}")
    return reels

completed_reels = load_completed_reels_from_disk()

def queue_worker():
    global active_task, task_queue, completed_reels
    while True:
        item = None
        with queue_lock:
            if task_queue:
                item = task_queue.pop(0)
                active_task["queue_remaining"] = len(task_queue)

        if not item:
            time.sleep(1)
            continue

        url, options = item
        active_task["status"] = "running"
        active_task["current_url"] = url
        active_task["message"] = "Starting process..."
        active_task["step"] = 1
        active_task["error"] = None
        active_task["result"] = None

        try:
            def on_status(msg, step, total):
                active_task["message"] = msg
                active_task["step"] = step
                active_task["total"] = total

            res = run_pipeline(url, options=options, status_callback=on_status)

            # Auto-Publish to Instagram if selected
            if options.get("auto_publish", False):
                active_task["message"] = f"Auto-publishing Reel {res.get('reel_id')} to Instagram..."
                active_task["step"] = 5
                active_task["total"] = 5
                try:
                    pub_ok = publish_to_instagram_sync(
                        res["video_path"],
                        res.get("description", ""),
                        reel_id=res.get("reel_id", ""),
                        auto_share=True
                    )
                    if pub_ok:
                        res["is_published"] = True
                        res["published_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                        pub_file = os.path.join(res.get("reel_folder", ""), "published.json")
                        with open(pub_file, "w", encoding="utf-8") as pf:
                            json.dump({"published": True, "published_at": res["published_at"]}, pf)
                except Exception as pe:
                    print(f"Auto-publish queue error: {pe}")

            active_task["status"] = "done"
            active_task["result"] = res

            with queue_lock:
                existing_idx = next((i for i, r in enumerate(completed_reels) if r.get("reel_id") == res.get("reel_id")), None)
                if existing_idx is not None:
                    completed_reels[existing_idx] = res
                else:
                    completed_reels.insert(0, res)

            time.sleep(1)
        except Exception as e:
            active_task["status"] = "error"
            active_task["error"] = str(e)
            print(f"Queue error: {e}")
            time.sleep(1)

threading.Thread(target=queue_worker, daemon=True).start()
start_dm_listener(enqueue_cb=enqueue_from_dm)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Keertan Editz — 4K Reel Studio</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background-color: #09090b;
            color: #f4f4f5;
        }
        .glow-purple {
            box-shadow: 0 0 50px -10px rgba(168, 85, 247, 0.25);
        }
        .border-glow:focus-within {
            border-color: rgba(168, 85, 247, 0.6);
            box-shadow: 0 0 25px -5px rgba(168, 85, 247, 0.4);
        }
        .glass-card {
            background: rgba(18, 18, 24, 0.85);
            backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.08);
        }
        /* Custom scrollbar */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: #09090b; }
        ::-webkit-scrollbar-thumb { background: #27272a; border-radius: 4px; }
    </style>
</head>
<body class="min-h-screen flex flex-col items-center justify-start p-4 sm:p-8">

    <!-- Ambient Glow Background -->
    <div class="fixed top-0 left-1/2 -translate-x-1/2 w-[700px] h-[350px] bg-purple-600/15 blur-[130px] pointer-events-none -z-10"></div>

    <!-- Main Container -->
    <div class="w-full max-w-5xl mx-auto flex flex-col items-center">
        
        <!-- Top Nav / Quick Action -->
        <div class="w-full flex items-center justify-between py-2 mb-4 text-xs text-zinc-400">
            <div class="flex items-center gap-2">
                <span class="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse"></span>
                <span class="font-medium text-zinc-300">Apple Silicon Hardware Engine Active</span>
            </div>
            <button 
                onclick="revealFolder('')" 
                class="hover:text-purple-300 flex items-center gap-1.5 transition bg-zinc-900/60 px-3 py-1.5 rounded-lg border border-zinc-800"
            >
                📂 Open Master Output Folder
            </button>
        </div>

        <!-- Header Branding -->
        <header class="flex flex-col items-center text-center mt-2 mb-8">
            <div class="relative mb-3">
                <div class="w-24 h-24 rounded-full border-2 border-purple-500/60 p-0.5 shadow-xl shadow-purple-500/40 overflow-hidden bg-black ring-4 ring-purple-500/10">
                    <img src="/template-logo" alt="Avatar" class="w-full h-full object-cover rounded-full">
                </div>
                <div class="absolute bottom-0 right-0 w-7 h-7 bg-blue-500 rounded-full flex items-center justify-center text-white text-xs font-bold border-2 border-[#09090b] shadow-md">
                    ✓
                </div>
            </div>
            <h1 class="text-3xl sm:text-4xl font-extrabold tracking-tight text-white flex items-center gap-2.5">
                keertan.editz
                <span class="text-xs font-semibold px-2.5 py-1 rounded-full bg-gradient-to-r from-purple-500/30 to-indigo-500/30 text-purple-300 border border-purple-500/40">Studio 4K</span>
            </h1>
            <p class="text-sm text-zinc-400 mt-1 max-w-md">Batch Reels Downloader • 4K UHD Master Upscaler • Automatic Template Compositor</p>
        </header>

        <!-- Generation Studio Card -->
        <div class="w-full glass-card rounded-2xl p-6 sm:p-8 glow-purple mb-8">
            
            <!-- Mode Selector (Single vs Batch) -->
            <div class="flex items-center justify-between pb-4 border-b border-zinc-800/80 mb-4">
                <div class="flex items-center gap-2">
                    <button id="tab-single" onclick="switchMode('single')" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-purple-600 text-white transition">Single Reel</button>
                    <button id="tab-batch" onclick="switchMode('batch')" class="px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-zinc-800/80 text-zinc-400 hover:text-white transition">Batch Queue (Multi-Link)</button>
                </div>
                <div class="flex items-center gap-3 text-xs flex-wrap justify-end">
                    <!-- Auto-Publish Direct Toggle -->
                    <label class="flex items-center gap-2 cursor-pointer bg-zinc-900 border border-purple-500/30 hover:border-purple-500/60 rounded-lg px-2.5 py-1 text-xs text-zinc-300 transition">
                        <input type="checkbox" id="auto-publish-toggle" checked class="w-3.5 h-3.5 accent-purple-600 rounded cursor-pointer">
                        <span class="font-semibold text-purple-300 flex items-center gap-1">
                            <span>⚡</span> Auto-Publish to Instagram
                        </span>
                    </label>

                    <!-- Resolution Picker -->
                    <label class="text-zinc-400 font-medium">Quality:</label>
                    <select id="quality-select" class="bg-zinc-900 border border-zinc-700/60 rounded-lg px-2.5 py-1 text-xs text-purple-300 focus:outline-none">
                        <option value="4k" selected>4K UHD (2160×3840)</option>
                        <option value="1080p">1080p Full HD (Fast)</option>
                    </select>
                </div>
            </div>

            <!-- Forms -->
            <form id="single-form" onsubmit="submitReel(event)">
                <div class="space-y-4">
                    <div>
                        <div class="flex items-center justify-between mb-2">
                            <label class="text-xs font-semibold uppercase tracking-wider text-zinc-400">Instagram Reel URL</label>
                            <button type="button" onclick="pasteClipboard('single')" class="text-xs text-purple-400 hover:text-purple-300 transition">📋 Paste from Clipboard</button>
                        </div>
                        <div class="relative flex items-center border border-zinc-800 focus-within:border-purple-500 rounded-xl bg-zinc-900/90 transition-all">
                            <span class="pl-4 text-purple-400">
                                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"></path></svg>
                            </span>
                            <input 
                                type="url" 
                                id="single-url" 
                                placeholder="https://www.instagram.com/reel/..." 
                                required 
                                class="w-full bg-transparent px-4 py-3.5 text-sm sm:text-base text-white placeholder-zinc-500 focus:outline-none"
                            >
                        </div>
                    </div>

                    <div class="flex justify-end pt-2">
                        <button 
                            type="submit" 
                            id="submit-single-btn" 
                            class="px-7 py-3 bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white font-semibold text-sm rounded-xl shadow-lg shadow-purple-600/30 transition-all transform active:scale-95 flex items-center gap-2"
                        >
                            <span>⚡ Generate Edit</span>
                        </button>
                    </div>
                </div>
            </form>

            <form id="batch-form" class="hidden space-y-4" onsubmit="submitBatch(event)">
                <div>
                    <div class="flex items-center justify-between mb-2">
                        <label class="text-xs font-semibold uppercase tracking-wider text-zinc-400">Batch Reel URLs (One link per line)</label>
                        <button type="button" onclick="pasteClipboard('batch')" class="text-xs text-purple-400 hover:text-purple-300 transition">📋 Paste</button>
                    </div>
                    <textarea 
                        id="batch-urls" 
                        rows="4" 
                        placeholder="https://www.instagram.com/reel/abc...&#10;https://www.instagram.com/reel/xyz..." 
                        required 
                        class="w-full bg-zinc-900/90 border border-zinc-800 focus:border-purple-500 rounded-xl p-3 text-sm text-white placeholder-zinc-500 focus:outline-none font-mono"
                    ></textarea>
                </div>
                <div class="flex justify-end">
                    <button 
                        type="submit" 
                        id="submit-batch-btn" 
                        class="px-7 py-3 bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white font-semibold text-sm rounded-xl shadow-lg shadow-purple-600/30 transition-all transform active:scale-95"
                    >
                        ⚡ Process Entire Batch
                    </button>
                </div>
            </form>

            <!-- Live Progress Bar -->
            <div id="progress-container" class="hidden mt-6 pt-6 border-t border-zinc-800">
                <div class="flex items-center justify-between text-xs mb-2">
                    <span id="progress-status" class="font-medium text-purple-300 flex items-center gap-2">
                        <svg class="animate-spin h-3.5 w-3.5 text-purple-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
                        </svg>
                        Initializing engine...
                    </span>
                    <span id="progress-step" class="text-zinc-400 font-mono">1/5</span>
                </div>
                <div class="w-full h-2.5 bg-zinc-800 rounded-full overflow-hidden p-0.5">
                    <div id="progress-bar" class="h-full bg-gradient-to-r from-purple-500 via-indigo-500 to-purple-400 rounded-full transition-all duration-300" style="width: 20%;"></div>
                </div>
                <div id="queue-badge" class="hidden mt-2 text-[11px] text-zinc-500 font-mono"></div>
            </div>

            <!-- Error Notification -->
            <div id="error-box" class="hidden mt-4 p-4 rounded-xl bg-red-950/40 border border-red-500/30 text-red-300 text-xs"></div>
        </div>

        <!-- Instagram DM Auto-Ingest (Auto-Process Shared Reels) -->
        <div class="w-full glass-card rounded-2xl p-6 glow-purple border border-zinc-800/80 mb-8 transition-all hover:border-purple-500/30">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-zinc-800 mb-4">
                <div class="flex items-center gap-3">
                    <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-purple-600 via-pink-600 to-rose-500 flex items-center justify-center text-lg shadow-md shadow-pink-500/30 shrink-0">
                        📥
                    </div>
                    <div>
                        <div class="flex items-center gap-2 flex-wrap">
                            <h2 class="text-base font-bold text-white">Instagram DM Auto-Ingest</h2>
                            <span id="dm-status-badge" class="text-[11px] font-semibold px-2.5 py-0.5 rounded-full bg-zinc-800 text-zinc-400 border border-zinc-700/60 flex items-center gap-1.5">
                                <span class="w-2 h-2 rounded-full bg-zinc-500"></span> Standby
                            </span>
                        </div>
                        <p class="text-xs text-zinc-400">When someone shares a reel to your Instagram account, the bot automatically copies the link, upscales to 4K, applies your template &amp; auto-publishes!</p>
                    </div>
                </div>

                <div class="flex items-center gap-2 shrink-0 flex-wrap">
                    <button 
                        type="button" 
                        id="dm-check-btn" 
                        onclick="checkDmNow()" 
                        class="px-3 py-1.5 text-xs font-semibold bg-zinc-800/90 hover:bg-zinc-700 text-zinc-300 rounded-lg flex items-center gap-1.5 transition"
                    >
                        🔄 Check Inbox Now
                    </button>
                    <button 
                        type="button" 
                        id="dm-login-btn" 
                        onclick="connectDmAccount()" 
                        class="px-3 py-1.5 text-xs font-semibold bg-purple-600/30 text-purple-300 hover:bg-purple-600/50 rounded-lg flex items-center gap-1.5 transition"
                    >
                        🔑 Connect Account
                    </button>
                    <!-- Toggle switch -->
                    <label class="relative inline-flex items-center cursor-pointer ml-1">
                        <input type="checkbox" id="dm-toggle" onchange="toggleDmListener(this.checked)" class="sr-only peer">
                        <div class="w-11 h-6 bg-zinc-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-zinc-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-purple-600"></div>
                    </label>
                </div>
            </div>

            <!-- Activity & Received Reels Log -->
            <div class="grid grid-cols-1 md:grid-cols-12 gap-4 text-xs">
                <!-- Activity Feed -->
                <div class="md:col-span-8 bg-zinc-900/60 rounded-xl p-3 border border-zinc-800/80">
                    <div class="flex items-center justify-between mb-2">
                        <span class="text-zinc-400 font-semibold uppercase tracking-wider text-[10px]">Real-Time DM Activity</span>
                        <span id="dm-last-checked" class="text-zinc-500 font-mono text-[10px]">Last checked: Never</span>
                    </div>
                    <div id="dm-activity-feed" class="space-y-1.5 max-h-32 overflow-y-auto font-mono text-[11px] pr-1">
                        <div class="text-zinc-500 italic py-2">Switch toggle ON to start listening for incoming shared reels in your Instagram DMs.</div>
                    </div>
                </div>

                <!-- Processed Count Stats -->
                <div class="md:col-span-4 bg-zinc-900/60 rounded-xl p-3 border border-zinc-800/80 flex flex-col justify-between">
                    <div>
                        <span class="text-zinc-400 font-semibold uppercase tracking-wider text-[10px]">Auto-Ingested Reels</span>
                        <div class="text-2xl font-extrabold text-white mt-1" id="dm-count-stat">0</div>
                        <p class="text-[11px] text-zinc-400 mt-1">Reels automatically captured from DMs &amp; sent through 4K pipeline.</p>
                    </div>
                    <div class="pt-2 text-[10px] text-zinc-500">
                        ⚡ Every captured reel gets 4K upscaling, watermark removal, branding &amp; auto-publishing.
                    </div>
                </div>
            </div>
        </div>

        <!-- Completed Edits Feed (Batch & Single Reels) -->
        <div id="completed-section" class="w-full space-y-6 mb-8">
            <div class="flex items-center justify-between pb-3 border-b border-zinc-800">
                <div>
                    <h2 class="text-xl font-bold text-white flex items-center gap-2">
                        <span class="text-purple-400">✨</span> Ready to Upload / Completed Edits
                        <span id="completed-count-badge" class="ml-2 text-xs font-semibold px-2.5 py-0.5 rounded-full bg-purple-500/20 text-purple-300 border border-purple-500/30">0</span>
                    </h2>
                    <p class="text-xs text-zinc-400">All batch and single reels stay here so you can review, copy captions, and upload each one at your own pace.</p>
                </div>
                <button 
                    onclick="revealFolder('')" 
                    class="px-3.5 py-1.5 text-xs font-semibold bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg flex items-center gap-1.5 transition"
                >
                    📂 Open Master Output Folder
                </button>
            </div>
            <div id="completed-cards-container" class="space-y-6">
                <!-- Dynamically populated reel cards -->
            </div>
        </div>

    </div>

    <script>
        let pollInterval = null;
        let igPollInterval = null;
        let activeIgReelId = null;

        function switchMode(mode) {
            if (mode === 'single') {
                document.getElementById('single-form').classList.remove('hidden');
                document.getElementById('batch-form').classList.add('hidden');
                document.getElementById('tab-single').className = 'px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-purple-600 text-white transition';
                document.getElementById('tab-batch').className = 'px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-zinc-800/80 text-zinc-400 hover:text-white transition';
            } else {
                document.getElementById('single-form').classList.add('hidden');
                document.getElementById('batch-form').classList.remove('hidden');
                document.getElementById('tab-batch').className = 'px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-purple-600 text-white transition';
                document.getElementById('tab-single').className = 'px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-zinc-800/80 text-zinc-400 hover:text-white transition';
            }
        }

        async function pasteClipboard(mode) {
            try {
                const text = await navigator.clipboard.readText();
                if (mode === 'single') {
                    document.getElementById('single-url').value = text.trim();
                } else {
                    document.getElementById('batch-urls').value = text.trim();
                }
            } catch (err) {
                alert('Please allow clipboard permissions or use Cmd+V.');
            }
        }

        function copyCaptionFromId(elemId, btn) {
            const elem = document.getElementById(elemId);
            if (!elem) return;
            navigator.clipboard.writeText(elem.value);
            const orig = btn.innerText;
            btn.innerText = '✓ Copied!';
            setTimeout(() => { btn.innerText = orig; }, 2000);
        }

        async function revealFolder(folder) {
            await fetch('/api/reveal', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ folder: folder ? decodeURIComponent(folder) : '' })
            });
        }

        async function submitReel(e) {
            e.preventDefault();
            const url = document.getElementById('single-url').value.trim();
            const quality = document.getElementById('quality-select').value;
            const autoPublish = document.getElementById('auto-publish-toggle') ? document.getElementById('auto-publish-toggle').checked : true;
            if (!url) return;
            startTask([url], { quality, auto_publish: autoPublish });
        }

        async function submitBatch(e) {
            e.preventDefault();
            const raw = document.getElementById('batch-urls').value.trim();
            const quality = document.getElementById('quality-select').value;
            const autoPublish = document.getElementById('auto-publish-toggle') ? document.getElementById('auto-publish-toggle').checked : true;
            const urls = raw.split(String.fromCharCode(10)).map(u => u.trim()).filter(u => u.length > 5);
            if (!urls.length) return;
            startTask(urls, { quality, auto_publish: autoPublish });
        }

        async function startTask(urls, options) {
            const progressContainer = document.getElementById('progress-container');
            const errorBox = document.getElementById('error-box');

            progressContainer.classList.remove('hidden');
            errorBox.classList.add('hidden');

            try {
                const res = await fetch('/api/enqueue', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ urls: urls, options: options })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.error || 'Failed to queue task');
                startPolling();
            } catch (err) {
                progressContainer.classList.add('hidden');
                errorBox.innerText = err.message;
                errorBox.classList.remove('hidden');
            }
        }

        function renderReelCard(reel) {
            const reelId = reel.reel_id || ('reel_' + Math.random().toString(36).substr(2, 9));
            const videoUrl = `/media/file?path=${encodeURIComponent(reel.video_path)}`;
            const videoDlUrl = `/media/file?path=${encodeURIComponent(reel.video_path)}&dl=1`;
            const thumbDlUrl = reel.thumbnail_path ? `/media/file?path=${encodeURIComponent(reel.thumbnail_path)}&dl=1` : '#';
            const folderName = reel.folder_name || reel.reel_folder || '';
            const qualityLabel = (reel.quality || '4K').toUpperCase();
            const sizeMb = reel.file_size_mb || 0;
            const isPub = !!reel.is_published;

            const div = document.createElement('div');
            div.id = `card-${reelId}`;
            div.className = 'w-full glass-card rounded-2xl p-6 sm:p-7 glow-purple border border-zinc-800/80 transition-all hover:border-purple-500/40 mb-6';
            div.innerHTML = `
                <div class="flex items-center justify-between pb-4 border-b border-zinc-800 mb-6">
                    <div class="flex items-center gap-3">
                        <span class="w-3 h-3 rounded-full ${isPub ? 'bg-purple-400 shadow-purple-500/50' : 'bg-emerald-400 shadow-emerald-500/50'} shadow-md"></span>
                        <div>
                            <h3 class="text-base font-bold text-white flex items-center gap-2 flex-wrap">
                                Reel: <span class="text-purple-300 font-mono">${reelId}</span>
                                <span class="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300 border border-purple-500/30">${qualityLabel} UHD</span>
                                <span id="pub-badge-${reelId}" class="${isPub ? '' : 'hidden'} text-[11px] font-semibold px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 flex items-center gap-1">
                                    ✓ Published to Instagram
                                </span>
                            </h3>
                            <p class="text-xs text-zinc-400 font-mono">Folder: ${folderName}</p>
                        </div>
                    </div>
                    <button 
                        onclick="revealFolder('${encodeURIComponent(reel.reel_folder || '')}')"
                        class="px-3 py-1.5 text-xs font-semibold bg-zinc-800/80 hover:bg-zinc-700 text-zinc-300 rounded-lg flex items-center gap-1.5 transition"
                    >
                        📂 Reveal in Finder
                    </button>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-12 gap-6 items-start">
                    <!-- Video Player Preview (9:16) -->
                    <div class="md:col-span-5 flex flex-col items-center">
                        <div class="w-full max-w-[270px] aspect-[9/16] bg-black rounded-xl overflow-hidden shadow-2xl border border-zinc-800/90 relative group">
                            <video controls preload="metadata" class="w-full h-full object-cover">
                                <source src="${videoUrl}" type="video/mp4">
                                Your browser does not support video playback.
                            </video>
                        </div>
                        <span class="text-xs text-zinc-400 mt-2 font-mono">${sizeMb} MB • 9:16 Vertical UHD</span>
                    </div>

                    <!-- Action Controls & Captions -->
                    <div class="md:col-span-7 space-y-4">
                        <!-- Instagram Publisher for this specific Reel -->
                        <div class="p-4 bg-gradient-to-r from-purple-950/40 via-fuchsia-950/30 to-pink-950/40 rounded-xl border border-pink-500/30 space-y-3">
                            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                                <div>
                                    <span class="text-xs font-bold text-white flex items-center gap-1.5">
                                        <span>📸</span> Official Instagram Publisher
                                    </span>
                                    <p class="text-[11px] text-zinc-400">Automates 9:16 vertical 4K upload, caption, hides likes &amp; publishes automatically</p>
                                </div>
                                <div class="flex flex-col sm:items-end gap-1.5 shrink-0">
                                    <button 
                                        type="button"
                                        id="ig-btn-${reelId}"
                                        onclick="publishReelToInstagram('${reelId}', '${encodeURIComponent(reel.video_path)}')" 
                                        class="px-4 py-2 ${isPub ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-gradient-to-r from-pink-600 via-rose-600 to-purple-600 hover:from-pink-500 hover:via-rose-500 hover:to-purple-500'} text-white font-semibold text-xs rounded-lg shadow-md shadow-pink-600/30 transition transform active:scale-95 flex items-center justify-center gap-1.5 shrink-0"
                                    >
                                        <span>${isPub ? '✓ Re-Publish to Instagram' : '🚀 Auto-Publish to Instagram'}</span>
                                    </button>
                                    <label class="flex items-center gap-1.5 text-[10px] text-zinc-400 cursor-pointer">
                                        <input type="checkbox" id="autoshare-check-${reelId}" checked class="rounded w-3 h-3 text-pink-600 accent-pink-600">
                                        <span>Auto-click Share (No manual review)</span>
                                    </label>
                                </div>
                            </div>
                            <div id="ig-status-box-${reelId}" class="hidden p-2.5 bg-black/40 border border-pink-500/20 rounded-lg text-xs space-y-1">
                                <div class="flex items-center gap-2 text-pink-300 font-medium">
                                    <span class="inline-block w-2 h-2 rounded-full bg-pink-500 animate-pulse"></span>
                                    <span id="ig-status-text-${reelId}">Starting upload...</span>
                                </div>
                                <p class="text-[10px] text-zinc-400">100% official browser session. Everything is automated without manual review.</p>
                            </div>
                        </div>

                        <!-- Package Assets & iPhone Sync -->
                        <div>
                            <label class="block text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-2">Package Assets</label>
                            <div class="p-3 bg-zinc-900/70 rounded-xl border border-zinc-800 space-y-2 text-xs">
                                <div class="flex items-center justify-between">
                                    <span class="text-zinc-300 font-mono truncate max-w-[220px]">${reel.video_name || 'video.mp4'}</span>
                                    <div class="flex items-center gap-1.5 shrink-0">
                                        <button type="button" id="sync-btn-${reelId}" onclick="syncReelPhotos('${encodeURIComponent(reel.video_path)}', 'sync-btn-${reelId}')" class="px-2.5 py-1 bg-cyan-600/30 text-cyan-300 hover:bg-cyan-600/50 rounded transition font-medium">📲 Sync to iPhone</button>
                                        <a href="${videoDlUrl}" download class="px-2.5 py-1 bg-purple-600/30 text-purple-300 hover:bg-purple-600/50 rounded transition font-medium">Download</a>
                                    </div>
                                </div>
                                ${reel.thumbnail_name ? `
                                <div class="flex items-center justify-between">
                                    <span class="text-zinc-300 font-mono truncate max-w-[220px]">${reel.thumbnail_name}</span>
                                    <a href="${thumbDlUrl}" download class="px-2.5 py-1 bg-zinc-800 text-zinc-300 hover:bg-zinc-700 rounded transition font-medium">Download 4K Cover</a>
                                </div>
                                ` : ''}
                            </div>
                        </div>

                        <!-- Pro Tip -->
                        <div class="p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-xs text-amber-300 flex items-start gap-2.5">
                            <span class="text-base shrink-0">🔥</span>
                            <div>
                                <span class="font-bold text-amber-200 block">Pro Tip for Millions of Views:</span>
                                <span class="text-[11px] text-zinc-300">Tap <strong>📲 Sync to iPhone</strong> to save to Camera Roll, select a <strong>Trending Audio</strong> in Instagram mobile app, and post for maximum algorithm reach!</span>
                            </div>
                        </div>

                        <!-- Caption Box -->
                        <div>
                            <div class="flex items-center justify-between mb-1.5">
                                <label class="text-xs font-semibold uppercase tracking-wider text-zinc-400">Post Description / Caption</label>
                                <button 
                                    type="button"
                                    onclick="copyCaptionFromId('caption-${reelId}', this)" 
                                    class="px-2.5 py-1 text-xs font-medium bg-purple-500/20 text-purple-300 hover:bg-purple-500/30 rounded-md transition"
                                >
                                    📋 Copy Caption
                                </button>
                            </div>
                            <textarea 
                                id="caption-${reelId}" 
                                rows="3" 
                                readonly 
                                class="w-full bg-zinc-900/70 border border-zinc-800 rounded-xl p-3 text-xs text-zinc-300 focus:outline-none resize-none font-mono"
                            >${reel.description || ''}</textarea>
                        </div>
                    </div>
                </div>
            `;
            return div;
        }

        function updateCompletedList(reels) {
            if (!Array.isArray(reels)) return;
            const container = document.getElementById('completed-cards-container');
            const badge = document.getElementById('completed-count-badge');
            if (badge) badge.innerText = reels.length;

            const emptyFeed = document.getElementById('empty-feed');
            if (reels.length > 0 && emptyFeed) {
                emptyFeed.remove();
            }

            // We iterate from end to start so when we prepend new ones, order remains descending (latest at top)
            for (let i = reels.length - 1; i >= 0; i--) {
                const reel = reels[i];
                const cardId = 'card-' + reel.reel_id;
                if (!document.getElementById(cardId)) {
                    const cardElem = renderReelCard(reel);
                    container.prepend(cardElem);
                }
            }
        }

        function renderEmptyFeed() {
            const container = document.getElementById('completed-cards-container');
            if (!container.children.length) {
                container.innerHTML = `
                    <div id="empty-feed" class="text-center py-12 text-zinc-500 text-sm glass-card rounded-2xl border border-zinc-800/60">
                        No completed reels yet. Submit a single reel or batch above to begin generating 4K edits!
                    </div>
                `;
            }
        }

        async function pollStatusCycle() {
            try {
                const res = await fetch('/api/status');
                const task = await res.json();

                if (task.completed_reels && task.completed_reels.length > 0) {
                    updateCompletedList(task.completed_reels);
                } else if (!task.completed_reels || task.completed_reels.length === 0) {
                    renderEmptyFeed();
                }

                const progressContainer = document.getElementById('progress-container');
                if (task.status === 'running') {
                    progressContainer.classList.remove('hidden');
                    document.getElementById('progress-status').innerText = task.message;
                    document.getElementById('progress-step').innerText = `${task.step}/${task.total}`;
                    const pct = Math.round((task.step / task.total) * 100);
                    document.getElementById('progress-bar').style.width = `${pct}%`;

                    const badge = document.getElementById('queue-badge');
                    if (task.queue_remaining > 0) {
                        badge.innerText = `Queue: ${task.queue_remaining} more reel(s) waiting`;
                        badge.classList.remove('hidden');
                    } else {
                        badge.classList.add('hidden');
                    }
                } else {
                    if (task.queue_remaining === 0) {
                        progressContainer.classList.add('hidden');
                    }
                    if (task.status === 'error' && task.error) {
                        const eb = document.getElementById('error-box');
                        eb.innerText = task.error;
                        eb.classList.remove('hidden');
                    }
                }
            } catch (e) {
                console.error('Status poll error', e);
            }
        }

        function startPolling() {
            pollStatusCycle();
        }

        async function publishReelToInstagram(reelId, encodedVideoPath) {
            const videoPath = decodeURIComponent(encodedVideoPath);
            const captionElem = document.getElementById('caption-' + reelId);
            const caption = captionElem ? captionElem.value : '';
            const btn = document.getElementById('ig-btn-' + reelId);
            const statusBox = document.getElementById('ig-status-box-' + reelId);
            const statusText = document.getElementById('ig-status-text-' + reelId);
            const autoShareCheck = document.getElementById('autoshare-check-' + reelId);
            const autoShare = autoShareCheck ? autoShareCheck.checked : true;

            if (!videoPath) return;

            btn.disabled = true;
            btn.innerHTML = '<span class="inline-block animate-spin mr-1.5">⏳</span> <span>Starting...</span>';
            statusBox.classList.remove('hidden');
            statusText.innerText = autoShare ? 'Connecting to browser & preparing auto-publish...' : 'Connecting to official browser...';

            try {
                const res = await fetch('/api/publish_instagram', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ video_path: videoPath, caption: caption, reel_id: reelId, auto_share: autoShare })
                });
                const data = await res.json();
                if (!data.success) {
                    btn.disabled = false;
                    btn.innerHTML = '<span>🚀 Auto-Publish to Instagram</span>';
                    statusText.innerText = 'Notice: ' + (data.error || 'Failed to start');
                    alert(data.error || 'Failed to start Instagram upload.');
                    return;
                }

                activeIgReelId = reelId;
                if (igPollInterval) clearInterval(igPollInterval);
                igPollInterval = setInterval(async () => {
                    try {
                        const sRes = await fetch('/api/ig_status');
                        const state = await sRes.json();
                        
                        if (state.reel_id && state.reel_id !== reelId) {
                            clearInterval(igPollInterval);
                            return;
                        }

                        if (state.message) {
                            statusText.innerText = state.message;
                        }

                        if (state.status === 'waiting_login') {
                            btn.innerHTML = '<span>🔑 Log In in Browser</span>';
                        } else if (state.status === 'attaching_video') {
                            btn.innerHTML = '<span class="inline-block animate-spin mr-1.5">📤</span> <span>Uploading Video...</span>';
                        } else if (state.status === 'filling_caption') {
                            btn.innerHTML = '<span>✍️ Adding Caption...</span>';
                        } else if (state.status === 'hiding_likes') {
                            btn.innerHTML = '<span>🔒 Hiding Like Counts...</span>';
                        } else if (state.status === 'sharing' || state.status === 'uploading_reel') {
                            btn.innerHTML = '<span class="inline-block animate-spin mr-1.5">🚀</span> <span>Publishing to Instagram...</span>';
                            btn.className = 'px-4 py-2 bg-gradient-to-r from-purple-600 to-pink-600 text-white font-semibold text-xs rounded-lg shadow-md flex items-center justify-center gap-1.5 shrink-0';
                        } else if (state.status === 'ready_for_review') {
                            clearInterval(igPollInterval);
                            btn.disabled = false;
                            btn.className = 'px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs rounded-lg shadow-md transition flex items-center justify-center gap-1.5 shrink-0';
                            btn.innerHTML = '<span>✓</span> <span>Ready in Instagram</span>';
                            statusText.innerHTML = '<strong class="text-emerald-400">Video & Caption Uploaded!</strong> Review the post and click <strong>Share</strong> in your Instagram window.';
                        } else if (state.status === 'shared') {
                            clearInterval(igPollInterval);
                            btn.disabled = false;
                            btn.className = 'px-4 py-2 bg-emerald-600 text-white font-semibold text-xs rounded-lg shadow-md flex items-center justify-center gap-1.5 shrink-0';
                            btn.innerHTML = '<span>🎉</span> <span>Published!</span>';
                            statusText.innerHTML = '<strong class="text-emerald-400">✓ Reel Successfully Published!</strong> Your video is live on Instagram.';
                            
                            const badge = document.getElementById('pub-badge-' + reelId);
                            if (badge) badge.classList.remove('hidden');

                            fetch('/api/mark_published', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ reel_id: reelId })
                            }).catch(() => {});
                        } else if (state.status === 'error') {
                            clearInterval(igPollInterval);
                            btn.disabled = false;
                            btn.innerHTML = '<span>🔄 Retry Publish</span>';
                            statusText.innerText = 'Notice: ' + state.message;
                        }
                    } catch (err) {
                        console.error('IG poll error', err);
                    }
                }, 1000);

            } catch (err) {
                btn.disabled = false;
                btn.innerHTML = '<span>🚀 Auto-Publish to Instagram</span>';
                alert('Could not start Instagram publisher: ' + err.message);
        }

        async function syncReelPhotos(encodedVideoPath, btnId) {
            const videoPath = decodeURIComponent(encodedVideoPath);
            const btn = document.getElementById(btnId);
            const originalText = btn ? btn.innerHTML : '📲 Sync to iPhone';
            if (btn) {
                btn.innerHTML = '<span>⏳ Syncing...</span>';
                btn.disabled = true;
            }
            try {
                const res = await fetch('/api/sync_photos', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ video_path: videoPath })
                });
                const data = await res.json();
                if (data.success) {
                    if (btn) {
                        btn.className = 'px-2.5 py-1 bg-emerald-600/30 text-emerald-300 rounded font-medium';
                        btn.innerHTML = '<span>✓ In Photos</span>';
                    }
                    alert(data.message || 'Video added to Apple Photos! It will appear on your iPhone Camera Roll automatically.');
                } else {
                    if (btn) {
                        btn.innerHTML = originalText;
                        btn.disabled = false;
                    }
                    alert('Could not sync: ' + (data.error || 'Unknown error'));
                }
            } catch (err) {
                if (btn) {
                    btn.innerHTML = originalText;
                    btn.disabled = false;
                }
                alert('Error syncing to Photos: ' + err.message);
            }
        }

        // DM Auto-Ingest JS Logic
        async function fetchDmStatus() {
            try {
                const res = await fetch('/api/dm/status');
                const data = await res.json();
                updateDmUI(data);
            } catch (err) {
                console.error('Error fetching DM status', err);
            }
        }

        function updateDmUI(data) {
            const toggle = document.getElementById('dm-toggle');
            if (toggle && toggle.checked !== data.enabled) {
                toggle.checked = data.enabled;
            }

            const badge = document.getElementById('dm-status-badge');
            if (badge) {
                if (data.status === 'listening') {
                    badge.className = 'text-[11px] font-semibold px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 flex items-center gap-1.5';
                    badge.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span> Listening for Shared Reels';
                } else if (data.status === 'checking') {
                    badge.className = 'text-[11px] font-semibold px-2.5 py-0.5 rounded-full bg-purple-500/20 text-purple-300 border border-purple-500/30 flex items-center gap-1.5';
                    badge.innerHTML = '<span class="w-2 h-2 rounded-full bg-purple-400 animate-spin"></span> Checking Inbox...';
                } else if (data.status === 'waiting_login') {
                    badge.className = 'text-[11px] font-semibold px-2.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30 flex items-center gap-1.5';
                    badge.innerHTML = '<span class="w-2 h-2 rounded-full bg-amber-400"></span> Login Required';
                } else {
                    badge.className = 'text-[11px] font-semibold px-2.5 py-0.5 rounded-full bg-zinc-800 text-zinc-400 border border-zinc-700/60 flex items-center gap-1.5';
                    badge.innerHTML = '<span class="w-2 h-2 rounded-full bg-zinc-500"></span> Standby';
                }
            }

            const lastChecked = document.getElementById('dm-last-checked');
            if (lastChecked && data.last_checked) {
                lastChecked.innerText = `Last checked: ${data.last_checked}`;
            }

            const countStat = document.getElementById('dm-count-stat');
            if (countStat) {
                countStat.innerText = data.found_count || 0;
            }

            const activityFeed = document.getElementById('dm-activity-feed');
            if (activityFeed && data.recent_activity && data.recent_activity.length > 0) {
                activityFeed.innerHTML = data.recent_activity.map(act => {
                    let color = 'text-zinc-400';
                    if (act.level === 'success') color = 'text-emerald-400';
                    if (act.level === 'warning') color = 'text-amber-400';
                    if (act.level === 'error') color = 'text-red-400';
                    return `<div class="${color} truncate"><span class="text-zinc-500">[${act.timestamp}]</span> ${act.message}</div>`;
                }).reverse().join('');
            }
        }

        async function toggleDmListener(enable) {
            try {
                const res = await fetch('/api/dm/toggle', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enable: enable })
                });
                const data = await res.json();
                updateDmUI(data);
            } catch (err) {
                alert('Could not toggle DM listener: ' + err.message);
            }
        }

        async function checkDmNow() {
            const btn = document.getElementById('dm-check-btn');
            const orig = btn ? btn.innerHTML : '🔄 Check Inbox Now';
            if (btn) {
                btn.innerHTML = '<span>⏳ Checking...</span>';
                btn.disabled = true;
            }
            try {
                await fetch('/api/dm/check_now', { method: 'POST' });
                setTimeout(fetchDmStatus, 2000);
            } catch (err) {
                console.error(err);
            } finally {
                setTimeout(() => {
                    if (btn) {
                        btn.innerHTML = orig;
                        btn.disabled = false;
                    }
                }, 2000);
            }
        }

        async function connectDmAccount() {
            try {
                await fetch('/api/dm/connect', { method: 'POST' });
                alert('Opening official Instagram window. Please log in if needed. Once logged in, DM Auto-Ingest will activate automatically.');
            } catch (err) {
                alert('Could not open login browser: ' + err.message);
            }
        }

        document.addEventListener('DOMContentLoaded', async () => {
            await pollStatusCycle();
            setInterval(pollStatusCycle, 2000);

            // Initialize DM Auto-Ingest status & periodic polling
            fetchDmStatus();
            setInterval(fetchDmStatus, 3000);
        });
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/template-logo')
def template_logo():
    logo_path = os.path.join(APP_DIR, 'logo.png')
    if not os.path.exists(logo_path):
        from PIL import Image
        tpl = Image.open(os.path.join(APP_DIR, 'template.png')).convert('RGBA')
        avatar = tpl.crop((215, 63, 360, 208))
        avatar.save(logo_path)
    return send_file(logo_path, mimetype='image/png')

@app.route('/api/status')
def get_status():
    with queue_lock:
        state = dict(active_task)
        state["completed_reels"] = list(completed_reels)
    return jsonify(state)

@app.route('/api/enqueue', methods=['POST'])
def enqueue():
    data = request.get_json() or {}
    urls = data.get("urls", [])
    options = data.get("options", {})

    valid_urls = [u.strip() for u in urls if u and ("instagram.com" in u or "instagr.am" in u)]
    if not valid_urls:
        return jsonify({"error": "No valid Instagram Reel URLs provided."}), 400

    with queue_lock:
        for u in valid_urls:
            task_queue.append((u, options))

    return jsonify({"success": True, "queued": len(valid_urls)})



@app.route('/api/publish_instagram', methods=['POST'])
def publish_instagram():
    data = request.get_json() or {}
    video_path = data.get('video_path', '').strip()
    caption = data.get('caption', '').strip()
    reel_id = data.get('reel_id', '').strip()
    auto_share = data.get('auto_share', True)
    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Video file not found."}), 400
    ok, msg = start_instagram_upload(video_path, caption, reel_id=reel_id, auto_share=auto_share)
    if not ok:
        return jsonify({"success": False, "error": msg}), 409
    return jsonify({"success": True})

@app.route('/api/mark_published', methods=['POST'])
def mark_published():
    data = request.get_json() or {}
    reel_id = data.get('reel_id', '').strip()
    with queue_lock:
        for r in completed_reels:
            if r.get('reel_id') == reel_id:
                r['is_published'] = True
                r['published_at'] = time.strftime("%Y-%m-%d %H:%M:%S")
                try:
                    folder = r.get('reel_folder', '')
                    if folder and os.path.exists(folder):
                        with open(os.path.join(folder, "published.json"), "w", encoding="utf-8") as pf:
                            json.dump({"published": True, "published_at": r['published_at']}, pf)
                except Exception:
                    pass
                break
    return jsonify({"success": True})

@app.route('/api/ig_status')
def ig_status():
    return jsonify(get_uploader_state())

@app.route('/api/dm/status')
def dm_status_endpoint():
    return jsonify(get_dm_state())

@app.route('/api/dm/toggle', methods=['POST'])
def dm_toggle_endpoint():
    data = request.get_json() or {}
    enable = data.get('enable', True)
    if enable:
        start_dm_listener(enqueue_cb=enqueue_from_dm)
    else:
        stop_dm_listener()
    return jsonify(get_dm_state())

@app.route('/api/dm/check_now', methods=['POST'])
def dm_check_now_endpoint():
    threading.Thread(target=poll_inbox_cycle, daemon=True).start()
    return jsonify({"success": True})

@app.route('/api/dm/connect', methods=['POST'])
def dm_connect_endpoint():
    open_instagram_login_browser()
    return jsonify({"success": True})

@app.route('/api/sync_photos', methods=['POST'])
def sync_photos():
    data = request.get_json() or {}
    video_path = data.get('video_path', '').strip()
    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Video file not found."}), 400
    try:
        # Import into macOS Photos app (syncs to iCloud / iPhone Camera Roll)
        script = f'tell application "Photos" to import POSIX file "{video_path}"'
        subprocess.run(['osascript', '-e', script], check=True)
        return jsonify({"success": True, "message": "Imported to Apple Photos! It will sync straight to your iPhone Camera Roll."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/reveal', methods=['POST'])
def reveal_finder():
    data = request.get_json() or {}
    folder = data.get('folder', '').strip()
    target = urllib.parse.unquote(folder) if folder else OUTPUT_DIR
    if not os.path.exists(target):
        target = OUTPUT_DIR
    subprocess.run(["open", target])
    return jsonify({"success": True})

@app.route('/media/file')
def serve_file():
    raw_path = request.args.get('path', '')
    path = urllib.parse.unquote(raw_path)
    if os.path.exists(path):
        ext = os.path.splitext(path)[1].lower()
        mime = 'video/mp4' if ext == '.mp4' else ('audio/mpeg' if ext == '.mp3' else 'image/jpeg')
        as_attachment = request.args.get('dl') == '1'
        return send_file(path, mimetype=mime, as_attachment=as_attachment, download_name=os.path.basename(path))
    return "File not found", 404

if __name__ == '__main__':
    port = 5050
    print(f"🚀 Keertan Editz Studio running on http://127.0.0.1:{port}")
    app.run(host='127.0.0.1', port=port, debug=False)
