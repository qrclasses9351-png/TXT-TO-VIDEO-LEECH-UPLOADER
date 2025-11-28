# main.py
# Clean, robust Telegram downloader bot (TXT -> PDF / Video)
# Features: yt-dlp with headers, ffmpeg fallback for m3u8 (Akamai), PDF support, thumbnails, safe filenames.

import os
import re
import sys
import time
import asyncio
import shlex
import logging
from pathlib import Path
from subprocess import PIPE

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode

# ---------- Config (edit vars.py to set) ----------
# It's expected you have vars.py with API_ID, API_HASH, BOT_TOKEN
try:
    from vars import API_ID, API_HASH, BOT_TOKEN
except Exception:
    print("Missing vars.py with API_ID, API_HASH, BOT_TOKEN. Exiting.")
    sys.exit(1)

# ---------- Settings ----------
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "/opt/render/project/src/downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s | %(levelname)s | %(message)s")

# default headers used for yt-dlp and ffmpeg (to mimic browser)
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "*/*",
    "Connection": "keep-alive"
}

# Limits
MIN_VIDEO_BYTES = 120_000        # treat smaller as failed (120 KB)
PDF_LIMIT_BYTES = 49 * 1024 * 1024  # 49 MB safe limit

# ---------- Bot init ----------
bot = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ---------- Utilities ----------
def safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[<>:"/\\|?*\n\r]+', "_", name).strip()
    if len(name) > max_len:
        name = name[:max_len].rsplit(" ", 1)[0]
    name = name.strip(" ._")
    return name or "file"

def path_for(name: str, ext: str):
    return os.path.join(DOWNLOAD_DIR, f"{name}{ext}")

def build_yt_dlp_headers(url: str) -> str:
    # Build --add-header args for yt-dlp
    host = ""
    try:
        host = url.split("/")[2]
    except:
        host = ""
    headers = {
        **DEFAULT_HEADERS,
        "Referer": url,
    }
    if host:
        headers["Origin"] = f"https://{host}"
    parts = []
    for k, v in headers.items():
        parts.append(f'--add-header "{k}: {v}"')
    return " ".join(parts)

def build_ffmpeg_header_args(url: str) -> str:
    # ffmpeg needs multiple -headers "Key: Value\r\n"
    headers = {
        **DEFAULT_HEADERS,
        "Referer": url
    }
    header_lines = "".join([f"{k}: {v}\r\n" for k, v in headers.items()])
    # ffmpeg CLI uses single -headers argument
    return shlex.quote(header_lines)

async def run_subprocess(cmd: str, timeout: int = 900):
    """
    Run a shell command asynchronously and return (returncode, stdout, stderr)
    """
    logging.info("Running shell command: %s", cmd if len(cmd) < 400 else cmd[:400] + " ...")
    proc = await asyncio.create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, b"", b"Timeout"
    return proc.returncode, stdout, stderr

def file_ok(path: str, min_bytes: int = MIN_VIDEO_BYTES) -> bool:
    try:
        st = os.path.getsize(path)
        return st >= min_bytes
    except:
        return False

# ---------- Download Handlers ----------

async def download_pdf(url: str, outname: str) -> str | None:
    """
    Try download PDF using requests streaming (with headers) or yt-dlp fallback.
    Returns path or None.
    """
    outpath = path_for(outname, ".pdf")
    headers = {**DEFAULT_HEADERS, "Referer": url}
    try:
        import requests
        with requests.get(url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(outpath, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
        if os.path.exists(outpath) and os.path.getsize(outpath) > 1000:
            return outpath
    except Exception as e:
        logging.warning("requests PDF download failed: %s", e)

    # fallback to yt-dlp
    ythead = build_yt_dlp_headers(url)
    cmd = f'yt-dlp {ythead} -o "{outpath}" "{url}" --no-check-certificate'
    rc, out, err = await run_subprocess(cmd, timeout=300)
    if rc == 0 and os.path.exists(outpath):
        return outpath
    # failed
    if os.path.exists(outpath):
        try:
            os.remove(outpath)
        except:
            pass
    return None

async def download_with_ytdlp(url: str, out_basename: str) -> str | None:
    """
    Try yt-dlp first with headers. Return downloaded filepath or None.
    """
    ythead = build_yt_dlp_headers(url)
    out_template = os.path.join(DOWNLOAD_DIR, f"{out_basename}.%(ext)s")
    cmd = f'yt-dlp {ythead} -f "best" -o "{out_template}" "{url}" --no-check-certificate'
    rc, out, err = await run_subprocess(cmd, timeout=900)
    # try to locate the file produced
    # yt-dlp may produce .mp4/.mkv/.webm etc.
    for ext in [".mp4", ".mkv", ".webm", ".mov", ".avi", ".ts"]:
        candidate = os.path.join(DOWNLOAD_DIR, f"{out_basename}{ext}")
        if os.path.exists(candidate) and file_ok(candidate):
            return candidate
    # sometimes yt-dlp writes with different ext; search by prefix
    for f in os.listdir(DOWNLOAD_DIR):
        if f.startswith(out_basename + "."):
            candidate = os.path.join(DOWNLOAD_DIR, f)
            if os.path.exists(candidate) and file_ok(candidate):
                return candidate
    logging.warning("yt-dlp did not produce a valid file for %s (rc=%s)", url, rc)
    return None

async def download_with_ffmpeg(url: str, out_basename: str) -> str | None:
    """
    Fallback using ffmpeg directly for m3u8/HLS or plain streams.
    """
    out_path = path_for(out_basename, ".mp4")
    header_lines = build_ffmpeg_header_args(url)  # already quoted
    # ffmpeg -headers "<headers>" -i "URL" -c copy out.mp4
    cmd = f'ffmpeg -y -headers {header_lines} -i "{url}" -c copy "{out_path}"'
    rc, out, err = await run_subprocess(cmd, timeout=1800)
    if rc == 0 and os.path.exists(out_path) and file_ok(out_path):
        return out_path
    # try with re-encoding if copy failed (sometimes necessary)
    cmd2 = f'ffmpeg -y -headers {header_lines} -i "{url}" -c:v libx264 -c:a aac -strict -2 "{out_path}"'
    rc2, out2, err2 = await run_subprocess(cmd2, timeout=1800)
    if rc2 == 0 and os.path.exists(out_path) and file_ok(out_path):
        return out_path
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except:
            pass
    logging.warning("ffmpeg failed for %s (rcs %s,%s)", url, rc, rc2 if 'rc2' in locals() else None)
    return None

async def download_video(url: str, out_basename: str) -> str | None:
    """
    Best-effort video downloader:
      1) yt-dlp with headers
      2) if fails -> ffmpeg (headers)
    Returns downloaded file path or None.
    """
    # 1) yt-dlp
    try:
        path = await download_with_ytdlp(url, out_basename)
        if path:
            logging.info("yt-dlp succeeded: %s", path)
            return path
    except Exception as e:
        logging.exception("yt-dlp crashed: %s", e)

    # 2) ffmpeg fallback
    try:
        path = await download_with_ffmpeg(url, out_basename)
        if path:
            logging.info("ffmpeg succeeded: %s", path)
            return path
    except Exception as e:
        logging.exception("ffmpeg crashed: %s", e)

    return None

# ---------- Telegram send helpers ----------
async def send_file_to_chat(chat_id: int, path: str, caption: str = "", thumb: str | None = None):
    """
    Send video or document depending on extension.
    """
    try:
        if path.lower().endswith(".pdf"):
            await bot.send_document(chat_id, document=path, caption=caption)
        else:
            # For big videos use send_video (supports streaming)
            await bot.send_video(chat_id, video=path, caption=caption, supports_streaming=True, thumb=thumb if thumb and os.path.exists(thumb) else None)
    except Exception as e:
        logging.exception("Failed to send file: %s", e)
        # attempt as document if send_video fails
        try:
            await bot.send_document(chat_id, document=path, caption=caption)
        except Exception as e2:
            logging.exception("Fallback send_document failed: %s", e2)

# ---------- Bot commands ----------
@bot.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Upload .TXT", callback_data="upload_files")]])
    text = "<b>Hi! Send /upload and upload a .txt file with one link per line (video/pdf)</b>"
    await message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@bot.on_callback_query(filters.regex(r"upload_files"))
async def cb_upload(_, query):
    await query.answer("Send /upload to start!", show_alert=True)

@bot.on_message(filters.command("upload"))
async def upload_cmd(client: Client, message: Message):
    prompt = await message.reply_text("📤 Send your .TXT file with links (one per line).")
    try:
        file_msg = await bot.listen(chat_id=message.chat.id, timeout=120)
    except Exception:
        await prompt.edit_text("❌ Timeout waiting for file.")
        return

    if not file_msg or not file_msg.document:
        await prompt.edit_text("❌ Please send a document (.txt).")
        return

    # download the txt
    downloaded = await file_msg.download()
    try:
        with open(downloaded, "r", encoding="utf-8", errors="ignore") as f:
            lines = [l.strip() for l in f.read().splitlines() if l.strip()]
    finally:
        try:
            os.remove(downloaded)
        except:
            pass

    if not lines:
        await prompt.edit_text("❌ File empty or no links found.")
        return

    await prompt.edit_text(f"📊 Found {len(lines)} links. Send starting index (1 for first).")
    try:
        idx_msg = await bot.listen(chat_id=message.chat.id, timeout=60)
        start_idx = int(idx_msg.text) if idx_msg and idx_msg.text.isdigit() else 1
    except Exception:
        start_idx = 1

    await bot.send_message(message.chat.id, "📝 Send batch name (or 'no'):")
    try:
        batch_msg = await bot.listen(chat_id=message.chat.id, timeout=60)
        batch_name = batch_msg.text if batch_msg and batch_msg.text else "Batch"
    except Exception:
        batch_name = "Batch"

    await bot.send_message(message.chat.id, "🎬 Enter video quality (e.g. 360 or 720) or 'no':")
    try:
        q_msg = await bot.listen(chat_id=message.chat.id, timeout=60)
        quality = q_msg.text if q_msg and q_msg.text else "720"
    except Exception:
        quality = "720"

    await bot.send_message(message.chat.id, "💬 Enter caption (or 'no'):")
    try:
        cap_msg = await bot.listen(chat_id=message.chat.id, timeout=120)
        caption_text = cap_msg.text if cap_msg and cap_msg.text else ""
    except Exception:
        caption_text = ""

    await bot.send_message(message.chat.id, "🖼 Send thumbnail URL or 'no':")
    try:
        t_msg = await bot.listen(chat_id=message.chat.id, timeout=120)
        thumb_input = t_msg.text if t_msg and t_msg.text else "no"
    except Exception:
        thumb_input = "no"

    thumbnail_local = None
    if thumb_input and thumb_input.lower() != "no" and thumb_input.startswith("http"):
        try:
            # try to wget thumbnail
            tnpath = os.path.join(DOWNLOAD_DIR, "thumb.jpg")
            cmd = f"wget -q -O {shlex.quote(tnpath)} {shlex.quote(thumb_input)}"
            rc, out, err = await run_subprocess(cmd, timeout=60)
            if rc == 0 and os.path.exists(tnpath):
                thumbnail_local = tnpath
        except Exception as e:
            logging.warning("Thumb fetch failed: %s", e)

    # Start processing links
    success = 0
    failed = 0

    for idx, line in enumerate(lines[start_idx - 1 : ], start=start_idx):
        # each line may have title and url; try to extract
        title, url = None, None
        m = re.search(r'(https?://\S+)', line)
        if m:
            url = m.group(1).rstrip(" ,;")
            title = line.replace(url, "").strip()
        else:
            # if line is just a domain or url without protocol
            if "." in line:
                url = "https://" + line.strip()
                title = ""
        if not url:
            await bot.send_message(message.chat.id, f"Skipping invalid line: {line}")
            failed += 1
            continue

        clean_title = safe_filename(title or url.split("/")[-1].split("?")[0], max_len=70)
        base_name = f"{str(idx).zfill(3)}) {clean_title}"

        progress = await bot.send_message(message.chat.id, f"⬇️ Downloading: {clean_title}\n{url}")

        try:
            # PDF branch
            if url.lower().endswith(".pdf"):
                out_pdf = await download_pdf(url, base_name)
                if out_pdf and os.path.exists(out_pdf) and os.path.getsize(out_pdf) <= PDF_LIMIT_BYTES:
                    await send_file_to_chat(message.chat.id, out_pdf, caption_text, thumbnail_local)
                    os.remove(out_pdf)
                    success += 1
                    await progress.edit(f"✅ PDF uploaded: {clean_title}")
                else:
                    failed += 1
                    await progress.edit(f"❌ PDF failed or too large: {clean_title}")
                await asyncio.sleep(1)
                continue

            # Video branch: attempt download
            got = await download_video(url, base_name)
            if got and os.path.exists(got) and file_ok(got):
                await send_file_to_chat(message.chat.id, got, caption_text, thumbnail_local)
                try:
                    os.remove(got)
                except:
                    pass
                success += 1
                await progress.edit(f"✅ Uploaded: {clean_title}")
            else:
                failed += 1
                await progress.edit(f"❌ Failed to download: {clean_title}")
        except Exception as e:
            logging.exception("Processing failed for %s: %s", url, e)
            failed += 1
            try:
                await progress.edit(f"❌ Error for {clean_title}: {e}")
            except:
                pass
        finally:
            await asyncio.sleep(1)

    await bot.send_message(
        message.chat.id,
        f"🎉 Done\n✅ Success: {success}\n❌ Failed: {failed}\n📦 Total: {success+failed}"
    )

# ---------- run ----------
if __name__ == "__main__":
    print("Starting bot...")
    bot.run()
