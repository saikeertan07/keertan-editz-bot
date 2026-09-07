import os
import re
import sys
import time
import json
import html
import subprocess
import urllib.request
import urllib.parse
import imageio_ffmpeg
from PIL import Image, ImageDraw
import numpy as np
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from ai_watermark_remover import detect_video_watermarks, detect_outro_watermark

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(APP_DIR, 'template.png')
if os.path.basename(APP_DIR) == 'App':
    PARENT_DIR = os.path.dirname(APP_DIR)
else:
    PARENT_DIR = APP_DIR
OUTPUT_DIR = os.path.join(PARENT_DIR, 'Output')
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
DESKTOP_DIR = OUTPUT_DIR



def get_reel_id(url):
    match = re.search(r'(?:reels?|p|share/reel)/([A-Za-z0-9_-]+)', url)
    if match:
        return match.group(1)
    cleaned = url.split('?')[0].rstrip('/')
    return cleaned.split('/')[-1] or f"reel_{int(time.time())}"

def extract_via_fastvideosave(reel_url):
    key = b'qwertyuioplkjhgf'
    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(reel_url.encode('utf-8')) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded_data) + encryptor.finalize()
    hex_url = encrypted.hex()

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'url': hex_url,
        'Origin': 'https://fastvideosave.net',
        'Referer': 'https://fastvideosave.net/'
    }

    req = urllib.request.Request('https://api.videodropper.app/allinone', headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))

    videos = data.get('video', [])
    if not videos or not videos[0].get('video'):
        raise ValueError("Video not returned by resolver service.")

    video_url = videos[0]['video']
    thumb_url = videos[0].get('thumbnail', '')
    return video_url, thumb_url

def download_video_stream(video_url, out_path):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.instagram.com/'
    }
    try:
        req = urllib.request.Request(video_url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp, open(out_path, 'wb') as f:
            while chunk := resp.read(64 * 1024):
                f.write(chunk)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 10000:
            return True
    except Exception as e:
        print(f"Direct stream download failed: {e}, trying proxy...")

    dl_url = f"https://dl.videodropper.app/?url={urllib.parse.quote(video_url)}"
    proxy_headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
        'Referer': 'https://fastvideosave.net/'
    }
    req2 = urllib.request.Request(dl_url, headers=proxy_headers)
    with urllib.request.urlopen(req2, timeout=40) as resp, open(out_path, 'wb') as f:
        while chunk := resp.read(64 * 1024):
            f.write(chunk)
    return True

def fetch_description(reel_url):
    match = re.search(r'(?:reels?|p|share/reel)/([A-Za-z0-9_-]+)', reel_url)
    reel_id = match.group(1) if match else None

    # Layer 1: Instagram Public Embed Frame (/embed/captioned/)
    # Official public iframe for external blog embeds, bypassing login wall completely
    if reel_id:
        try:
            embed_url = f"https://www.instagram.com/p/{reel_id}/embed/captioned/"
            req = urllib.request.Request(embed_url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9'
            })
            content = urllib.request.urlopen(req, timeout=6).read().decode('utf-8', errors='ignore')
            idx = content.find('class="Caption"')
            if idx != -1:
                idx_start = content.find('</a>', idx)
                if idx_start != -1:
                    idx_start += 4
                    idx_comments = content.find('class="CaptionComments"', idx_start)
                    if idx_comments != -1:
                        idx_end = content.rfind('<', idx_start, idx_comments)
                    else:
                        idx_end = content.find('</div>', idx_start)
                    raw = content[idx_start:idx_end] if idx_end != -1 else content[idx_start:idx_start+1000]
                    text = re.sub(r'<br\s*/?>', '\n', raw)
                    text = re.sub(r'<[^>]+>', '', text)
                    text = html.unescape(text).strip()
                    if text and len(text) > 0 and text.lower() != 'instagram':
                        return text
        except Exception as e:
            print(f"Embed caption extraction failed: {e}")

    # Layer 2: Instagram Official oEmbed API
    try:
        oembed_url = f"https://www.instagram.com/api/v1/oembed/?url={urllib.parse.quote(reel_url)}"
        req = urllib.request.Request(oembed_url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'application/json'
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            title = data.get('title', '').strip()
            if title and title.lower() != 'instagram':
                return title
    except Exception as e:
        print(f"oEmbed caption extraction failed: {e}")

    # Layer 3: yt-dlp native metadata extractor
    try:
        cmd = [sys.executable, '-m', 'yt_dlp', '--get-description', '--no-warnings', reel_url]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8)
        if res.returncode == 0:
            desc = res.stdout.strip()
            if desc and desc.lower() != 'none' and desc.lower() != 'instagram':
                return desc
    except Exception as e:
        print(f"yt-dlp caption extraction failed: {e}")

    # Layer 4: Meta & Social Crawler Open Graph tags
    for ua in [
        'facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)',
        'Twitterbot/1.0',
        'TelegramBot (like TwitterBot)',
        'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'
    ]:
        try:
            req = urllib.request.Request(reel_url, headers={'User-Agent': ua})
            content = urllib.request.urlopen(req, timeout=6).read().decode('utf-8', errors='ignore')
            m = re.search(r'<meta property=\"og:title\" content=\"(.*?)\"', content, re.DOTALL)
            if m:
                raw = html.unescape(m.group(1))
                if ': \"' in raw:
                    cand = raw.split(': \"', 1)[1].rstrip('\"').strip()
                    if cand and cand.lower() != 'instagram':
                        return cand
                elif ': ' in raw:
                    cand = raw.split(': ', 1)[1].strip()
                    if cand and cand.lower() != 'instagram':
                        return cand
            m2 = re.search(r'<meta property=\"og:description\" content=\"(.*?)\"', content, re.DOTALL)
            if m2:
                desc = html.unescape(m2.group(1))
                if ': \"' in desc:
                    cand = desc.split(': \"', 1)[1].rstrip('\"').rstrip('. ').strip()
                    if cand and cand.lower() != 'instagram':
                        return cand
        except Exception:
            continue

    return "No caption provided by creator."

def run_pipeline(reel_url, options=None, status_callback=None):
    if options is None:
        options = {}

    quality = options.get('quality', '4k') # '4k' or '1080p'
    rounded_corners = options.get('rounded_corners', False)

    total_steps = 5

    def update(msg, step):
        if status_callback:
            status_callback(msg, step, total_steps)
        print(f"[{step}/{total_steps}] {msg}")

    reel_id = get_reel_id(reel_url)
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    
    # 1. Prepare Dedicated Folder
    reel_folder = os.path.join(OUTPUT_DIR, f"Reel_{reel_id}")
    os.makedirs(reel_folder, exist_ok=True)
    raw_video_path = f"/tmp/raw_reel_{reel_id}.mp4"

    # Step 1: Download Video Stream
    update("Resolving high-quality video stream...", 1)
    download_success = False

    try:
        v_url, t_url = extract_via_fastvideosave(reel_url)
        update("Downloading video stream...", 1)
        download_video_stream(v_url, raw_video_path)
        download_success = os.path.exists(raw_video_path) and os.path.getsize(raw_video_path) > 10000
    except Exception as e:
        print(f"FastVideoSave resolver error: {e}")

    if not download_success:
        try:
            update("Connecting via secondary stream extractor...", 1)
            cmd_dl = [
                sys.executable, '-m', 'yt_dlp',
                '--ffmpeg-location', exe,
                '-f', 'bestvideo+bestaudio/best',
                reel_url,
                '-o', raw_video_path,
                '--force-overwrites',
                '--no-warnings'
            ]
            subprocess.run(cmd_dl, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            download_success = os.path.exists(raw_video_path) and os.path.getsize(raw_video_path) > 10000
        except Exception as e:
            print(f"yt-dlp error: {e}")

    if not download_success:
        raise RuntimeError("Unable to download this Reel. Instagram may have restricted access to this content.")

    # Step 2: Extract Caption
    update("Fetching post description & creator caption...", 2)
    description = fetch_description(reel_url)
    desc_desktop_path = os.path.join(reel_folder, f"keertan_editz_{reel_id}_description.txt")
    with open(desc_desktop_path, 'w', encoding='utf-8') as f:
        f.write(description + "\n")

    # Step 3: Configure 4K / 1080p Layout
    update(f"Configuring {quality.upper()} branding template...", 3)
    if quality == '1080p':
        CANVAS_W, CANVAS_H = 1080, 1920
        scale_ratio = 1080 / 2160
    else:
        CANVAS_W, CANVAS_H = 2160, 3840
        scale_ratio = 1.0

    # Load template and isolate branding header
    tpl = Image.open(TEMPLATE_PATH).convert('RGBA')
    tpl_scaled = tpl.resize((CANVAS_W, CANVAS_H), Image.Resampling.LANCZOS)
    t_arr = np.array(tpl_scaled)
    mask = (t_arr[:, :, :3] > 15).any(axis=2).astype(np.uint8) * 255
    header_img = tpl_scaled.copy()
    header_img.putalpha(Image.fromarray(mask))

    bbox = header_img.getbbox()
    if not bbox:
        bbox = (0, 0, CANVAS_W, int(500 * scale_ratio))
    header_cropped = header_img.crop(bbox)
    bbox_h = bbox[3] - bbox[1]

    # Shift header down (+250 px in 4K, scaled for 1080p)
    header_new_y = bbox[1] + int(250 * scale_ratio)
    header_bottom = header_new_y + bbox_h

    # Robust multi-frame content bounds detection
    # Sample 4 timestamps to find true non-black content bounds across scene cuts
    timestamps = [1.0, 3.0, 5.0, 8.0]
    det_min_x, det_min_y = 999999, 999999
    det_max_x, det_max_y = 0, 0
    raw_dim_w, raw_dim_h = 1080, 1920

    for ts in timestamps:
        df_path = f"/tmp/det_{reel_id}_{ts}.jpg"
        subprocess.run([exe, '-y', '-ss', str(ts), '-i', raw_video_path, '-vframes', '1', df_path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(df_path):
            try:
                df = Image.open(df_path).convert('RGB')
                raw_dim_w, raw_dim_h = df.size
                df_arr = np.array(df)
                v_non_black = (df_arr > 18).any(axis=2)
                v_rows = np.where(v_non_black.any(axis=1))[0]
                v_cols = np.where(v_non_black.any(axis=0))[0]
                if len(v_rows) > 0 and len(v_cols) > 0:
                    det_min_x = min(det_min_x, int(v_cols[0]))
                    det_min_y = min(det_min_y, int(v_rows[0]))
                    det_max_x = max(det_max_x, int(v_cols[-1]))
                    det_max_y = max(det_max_y, int(v_rows[-1]))
            except Exception:
                pass

    if det_max_x > det_min_x and det_max_y > det_min_y:
        crop_x = det_min_x
        crop_y = det_min_y
        crop_w = det_max_x - det_min_x + 1
        crop_h = det_max_y - det_min_y + 1
    else:
        crop_x, crop_y = 0, 0
        crop_w, crop_h = raw_dim_w, raw_dim_h

    # If non-black area is >= 94% of raw video, keep 100% full original frame
    if crop_w >= raw_dim_w * 0.94 and crop_h >= raw_dim_h * 0.94:
        crop_x, crop_y = 0, 0
        crop_w, crop_h = raw_dim_w, raw_dim_h

    crop_w = max(2, (crop_w // 2) * 2)
    crop_h = max(2, (crop_h // 2) * 2)
    crop_x = (crop_x // 2) * 2
    crop_y = (crop_y // 2) * 2

    # Aspect Ratio Preservation Logic
    content_aspect = crop_w / float(crop_h)

    if content_aspect <= 0.65:
        # Full 9:16 Vertical Instagram Reel:
        # Video fills 100% of the canvas; brand header overlays gracefully on top
        target_w = CANVAS_W
        target_h = CANVAS_H
        vid_x = 0
        vid_y = 0
        is_vertical_reel = True
        
        # Transparent header overlay template
        overlay_img = Image.new('RGBA', (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        overlay_img.paste(header_cropped, (bbox[0], header_new_y), header_cropped)
        overlay_path = f"/tmp/overlay_{reel_id}_{quality}.png"
        overlay_img.save(overlay_path)
    else:
        # Landscape (16:9) or Square (1:1) video embedded in a Reel:
        # Video scaled to width, centered in remaining space below header on black background
        target_w = CANVAS_W
        target_h = int(crop_h * (CANVAS_W / float(crop_w)))
        target_h = (target_h // 2) * 2
        
        avail_space = CANVAS_H - header_bottom
        vid_x = 0
        center_y = header_bottom + max(0, (avail_space - target_h) // 2)
        up_shift = int(220 * scale_ratio)
        vid_y = max(header_bottom + int(120 * scale_ratio), center_y - up_shift)
        vid_y = (vid_y // 2) * 2
        is_vertical_reel = False

        bg_img = Image.new('RGB', (CANVAS_W, CANVAS_H), (0, 0, 0))
        bg_img.paste(header_cropped, (bbox[0], header_new_y), header_cropped)
        bg_path = f"/tmp/bg_{reel_id}_{quality}.png"
        bg_img.save(bg_path)

    # AI Watermark & Logo Detection
    update("Scanning video with AI to detect and remove watermarks...", 3)
    wm_boxes = detect_video_watermarks(
        raw_video_path,
        crop_box=(crop_x, crop_y, crop_w, crop_h),
        target_size=(target_w, target_h)
    )
    
    delogo_chain = []
    if wm_boxes:
        update(f"AI detected {len(wm_boxes)} watermark(s) — applying inpainting delogo...", 3)
        for (bx, by, bw, bh) in wm_boxes:
            delogo_chain.append(f"delogo=x={bx}:y={by}:w={bw}:h={bh}:show=0")
    
    delogo_filter_str = ("," + ",".join(delogo_chain)) if delogo_chain else ""

    # Outro Watermark Detection (CapCut / TikTok ending)
    outro_trim = detect_outro_watermark(raw_video_path)
    if outro_trim:
        print(f"Auto-trimmed ending outro watermark at {outro_trim}s")

    # Step 3: Render Video with Apple Silicon Hardware Acceleration
    bitrate = "35M" if quality == '4k' else "12M"
    update(f"Rendering {quality.upper()} video with CAS sharpening...", 3)
    out_video_name = f"keertan_editz_{reel_id}_{quality}.mp4"
    out_video_path = os.path.join(reel_folder, out_video_name)

    if is_vertical_reel:
        filter_complex = (
            f'[0:v]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_w}:{target_h}:flags=lanczos{delogo_filter_str},cas=0.4,eq=contrast=1.03:brightness=0.01:saturation=1.06[vid];'
            f'[1:v]scale={CANVAS_W}:{CANVAS_H}:flags=lanczos[hdr];'
            f'[vid][hdr]overlay=0:0:shortest=1[outv]'
        )
        template_input = overlay_path
    else:
        filter_complex = (
            f'[0:v]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_w}:{target_h}:flags=lanczos{delogo_filter_str},cas=0.4,eq=contrast=1.03:brightness=0.01:saturation=1.06[vid];'
            f'[1:v]scale={CANVAS_W}:{CANVAS_H}:flags=lanczos[bg];'
            f'[bg][vid]overlay={vid_x}:{vid_y}:shortest=1[outv]'
        )
        template_input = bg_path

    cmd_render = [
        exe, '-y',
        '-i', raw_video_path,
        '-loop', '1', '-i', template_input,
        '-filter_complex', filter_complex,
        '-map', '[outv]',
        '-map', '0:a?',
        '-c:v', 'h264_videotoolbox',
        '-b:v', bitrate,
        '-color_primaries', 'bt709',
        '-color_trc', 'bt709',
        '-colorspace', 'bt709',
        '-c:a', 'aac',
        '-b:a', '320k',
        '-movflags', '+faststart',
        '-shortest'
    ]

    if outro_trim:
        cmd_render.extend(['-to', str(outro_trim)])

    cmd_render.append(out_video_path)

    res_render = subprocess.run(cmd_render, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res_render.returncode != 0:
        cmd_render[cmd_render.index('h264_videotoolbox')] = 'libx264'
        subprocess.run(cmd_render, check=True)

    # Step 4: Extract 4K Cover Thumbnail
    update(f"Generating {quality.upper()} cover thumbnail...", 4)
    out_thumb_name = f"keertan_editz_{reel_id}_{quality}_thumbnail.jpg"
    out_thumb_path = os.path.join(reel_folder, out_thumb_name)
    subprocess.run([
        exe, '-y',
        '-ss', '00:00:00.5',
        '-i', out_video_path,
        '-vframes', '1',
        '-q:v', '1',
        out_thumb_path
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Step 5: Prepare Official Instagram Publishing Package
    update("Preparing Official Instagram publishing package...", 5)

    return {
        "reel_id": reel_id,
        "quality": quality,
        "reel_folder": reel_folder,
        "folder_name": os.path.basename(reel_folder),
        "video_path": out_video_path,
        "thumbnail_path": out_thumb_path,
        "description_path": desc_desktop_path,
        "description": description,
        "video_name": out_video_name,
        "thumbnail_name": out_thumb_name,
        "description_name": os.path.basename(desc_desktop_path),
        "file_size_mb": round(os.path.getsize(out_video_path) / (1024 * 1024), 1),
        "instagram_create_url": "https://www.instagram.com/create/style/"
    }
