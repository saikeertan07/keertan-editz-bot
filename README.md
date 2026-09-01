# Keertan Editz Bot 🎬

> **Automated Video Processing, AI Inpainting & Distribution Pipeline**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?logo=python)](https://www.python.org/)
[![OpenCV](https://img.shields.io/badge/OpenCV-Computer%20Vision-5C3EE8.svg?logo=opencv)](https://opencv.org/)
[![Playwright](https://img.shields.io/badge/Playwright-Browser%20Automation-2EAD33.svg?logo=playwright)](https://playwright.dev/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-Video%20Processing-007808.svg?logo=ffmpeg)](https://ffmpeg.org/)
[![Security](https://img.shields.io/badge/Security-Zero%20Credentials%20Stored-brightgreen.svg)](#-security--credentials)
[![License](https://img.shields.io/badge/License-MIT-gray.svg)](LICENSE)

An autonomous Python pipeline that monitors incoming requests, decrypts video streams, removes watermarks using Computer Vision inpainting, applies custom brand styling, and automatically publishes reels via headless browser automation.

---

## ⚡ How It Works

```
Link Ingest (DM / Web UI)
       │
       ▼
Stream Decryption (AES-128 & FFmpeg)
       │
       ▼
AI Watermark Removal (OpenCV Inpainting)
       │
       ▼
Brand Compositing (Custom Canvas & Audio)
       │
       ▼
Automated Publishing (Playwright Headless)
```

---

## 🛠️ Key Features

* **AI Watermark Removal**: Uses OpenCV Telea inpainting (`cv2.inpaint`) to detect and remove burned-in logos by reconstructing pixels from surrounding data.
* **Stream Decryption**: Handles token-protected video streams using AES-128 cryptographic decryption.
* **Automated Publishing**: Uses Microsoft Playwright to safely upload and publish reels through an automated, headless browser session.
* **24/7 Service Daemon**: Runs continuously in the background with automatic error recovery, activity logging, and a local web dashboard (`localhost:5001`).

---

## 📁 Project Structure

```text
keertan-editz-bot/
├── ai_watermark_remover.py   # OpenCV watermark detection & pixel inpainting
├── pipeline.py               # Video download, stream decryption & compositing
├── instagram_publisher.py    # Headless Playwright browser automation
├── dm_listener.py            # Real-time message listener for incoming video links
├── server.py                 # Local web dashboard & manual job trigger
├── manage_service.sh         # Daemon control script (start, stop, status)
├── template.png              # Custom video frame branding asset
├── logo.png                  # Watermark / channel logo asset
├── .env.example              # Configuration template (no credentials)
└── .gitignore                # Strictly excludes logs, sessions & tokens
```

---

## 🔒 Security & Credentials

* **Zero Hardcoded Secrets**: This repository does not store any passwords, session tokens, cookies, or API keys.
* **External Sessions**: All authentication data and browser cookies are stored outside the repository directory and are strictly excluded via `.gitignore`.
* **Safe Configuration**: A template `.env.example` is provided for local setup without exposing sensitive data.

---

## 🚀 Quick Start

### 1. Installation
```bash
# Clone the repository
git clone https://github.com/saikeertan07/keertan-editz-bot.git
cd keertan-editz-bot

# Install required dependencies
pip install opencv-python numpy pillow playwright cryptography imageio-ffmpeg
playwright install chromium
```

### 2. Configure Environment
```bash
cp .env.example .env
```

### 3. Run
* **Launch the Web Dashboard**:
  ```bash
  python3 server.py
  ```
  *(Opens at `http://localhost:5001`)*

* **Run as a Background Service**:
  ```bash
  ./manage_service.sh start
  ```

---

## 📄 License
Distributed under the [MIT License](LICENSE).
