import os
import cv2
import numpy as np
import imageio_ffmpeg
import subprocess

def detect_video_watermarks(video_path, crop_box=None, target_size=None):
    """
    AI / Computer Vision Watermark Detector:
    Uses multi-frame temporal variance analysis and edge persistence
    to automatically identify static or semi-static watermarks, handles,
    and logos across video frames.
    
    Returns: list of (x, y, w, h) bounding boxes in target_size coordinates.
    """
    if not os.path.exists(video_path):
        return []

    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    duration = total_frames / fps if fps > 0 else 0

    if total_frames < 15 or duration < 1.0:
        cap.release()
        return []

    # Sample 8 frames across the video (skip start and end transitions)
    sample_timestamps = [0.15, 0.25, 0.35, 0.50, 0.65, 0.75, 0.85, 0.90]
    frames = []
    
    for pct in sample_timestamps:
        frame_idx = int(total_frames * pct)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if ret and frame is not None:
            frames.append(frame)
    cap.release()

    if len(frames) < 4:
        return []

    orig_h, orig_w = frames[0].shape[:2]

    # If crop_box is provided (x, y, w, h), crop frames first
    if crop_box:
        cx, cy, cw, ch = crop_box
        cx = max(0, min(cx, orig_w - 10))
        cy = max(0, min(cy, orig_h - 10))
        cw = min(cw, orig_w - cx)
        ch = min(ch, orig_h - cy)
        frames = [f[cy:cy+ch, cx:cx+cw] for f in frames]
    else:
        cx, cy, cw, ch = 0, 0, orig_w, orig_h

    if len(frames) == 0 or frames[0].size == 0:
        return []

    base_h, base_w = frames[0].shape[:2]

    # Downscale for fast, robust feature extraction
    proc_w = 720
    scale = proc_w / float(base_w)
    proc_h = int(base_h * scale)

    resized_grays = [cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (proc_w, proc_h)) for f in frames]

    # 1. Temporal Variance: Moving content has high variance; static watermarks have very low variance
    stack = np.stack(resized_grays, axis=0).astype(np.float32)
    temporal_variance = np.std(stack, axis=0)

    # 2. Edge Persistence: Watermarks/text have sharp edges present across all frames
    edges = [cv2.Canny(g, 60, 160) for g in resized_grays]
    edge_stack = np.stack(edges, axis=0).astype(np.float32)
    persistent_edges = np.min(edge_stack, axis=0) # Edge must exist in all sampled scenes

    # 3. Static Watermark Candidate Mask
    watermark_mask = (persistent_edges > 80) & (temporal_variance < 32)

    # 4. Corner / Margin Spatial Prior
    # Watermarks typically live in the outer corners or along the bottom/top borders
    spatial_weight = np.zeros((proc_h, proc_w), dtype=np.float32)
    
    # Corners & borders: top 25%, bottom 28%, left 25%, right 25%
    top_border = int(proc_h * 0.25)
    bottom_border = int(proc_h * 0.72)
    left_border = int(proc_w * 0.25)
    right_border = int(proc_w * 0.75)

    spatial_weight[:top_border, :] = 1.0
    spatial_weight[bottom_border:, :] = 1.0
    spatial_weight[:, :left_border] = 1.0
    spatial_weight[:, right_border:] = 1.0

    weighted_mask = watermark_mask.astype(np.float32) * spatial_weight

    # 5. Connect adjacent letters/symbols into a single watermark box
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 14))
    dilated = cv2.dilate((weighted_mask * 255).astype(np.uint8), kernel, iterations=2)

    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    detected_boxes = []

    target_w, target_h = target_size if target_size else (base_w, base_h)
    scale_to_target_x = target_w / float(proc_w)
    scale_to_target_y = target_h / float(proc_h)

    for cnt in contours:
        bx, by, bw, bh = cv2.boundingRect(cnt)
        
        # Filter noise and entire-screen boxes
        min_w = int(proc_w * 0.05)   # At least 5% width (e.g. 36px)
        max_w = int(proc_w * 0.40)   # At most 40% width
        min_h = int(proc_h * 0.015)  # At least 1.5% height
        max_h = int(proc_h * 0.14)   # At most 14% height

        if bw >= min_w and bw <= max_w and bh >= min_h and bh <= max_h:
            # Check edge stroke density inside the candidate region
            candidate_edges = persistent_edges[by:by+bh, bx:bx+bw]
            edge_density = np.sum(candidate_edges > 60) / float(bw * bh)
            
            # Watermarks (text, handles, glyphs) have multiple stroke edges (density >= 0.045)
            # Smooth architectural lines/shadows have much lower density
            if edge_density < 0.045:
                continue

            # Must be in corners or margin zones
            in_corners_or_margin = (
                by < proc_h * 0.20 or                     # Top margin
                by > proc_h * 0.75 or                     # Bottom margin
                (bx < proc_w * 0.22 and by < proc_h * 0.35) or # Top-left
                (bx > proc_w * 0.70 and by > proc_h * 0.65) or # Bottom-right
                (bx < proc_w * 0.22 and by > proc_h * 0.65)    # Bottom-left
            )
            if not in_corners_or_margin:
                continue

            # Map back to target coordinates
            tx = int(bx * scale_to_target_x)
            ty = int(by * scale_to_target_y)
            tw = int(bw * scale_to_target_x)
            th = int(bh * scale_to_target_y)

            # Add 10% padding for clean inpainting border blend
            pad_x = max(4, int(tw * 0.10))
            pad_y = max(4, int(th * 0.12))

            fx = max(2, tx - pad_x)
            fy = max(2, ty - pad_y)
            fw = min(target_w - fx - 2, tw + 2 * pad_x)
            fh = min(target_h - fy - 2, th + 2 * pad_y)

            # Ensure even dimensions for FFmpeg delogo filter
            fw = (fw // 2) * 2
            fh = (fh // 2) * 2
            fx = (fx // 2) * 2
            fy = (fy // 2) * 2

            if fw >= 16 and fh >= 16:
                detected_boxes.append((fx, fy, fw, fh))

    return detected_boxes

def detect_outro_watermark(video_path):
    """
    Detects if the video ends with a CapCut / TikTok outro screen.
    Returns: float (timestamp to trim end) or None.
    """
    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    duration = total_frames / fps if fps > 0 else 0

    if duration < 5.0: # Don't cut short clips
        cap.release()
        return None

    # Check the last 2 seconds
    check_frames = int(fps * 2.0)
    start_check_idx = max(0, total_frames - check_frames)
    
    black_count = 0
    total_checked = 0

    for idx in range(start_check_idx, total_frames, max(1, int(fps * 0.25))):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if ret and frame is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # CapCut outros typically have 85%+ black background
            black_ratio = np.mean(gray < 25)
            if black_ratio > 0.82:
                black_count += 1
            total_checked += 1

    cap.release()

    if total_checked > 0 and (black_count / total_checked) > 0.70:
        trim_point = round(max(0.5, duration - 2.2), 2)
        return trim_point
    return None
