# Don't Remove Credit Tg - https://t.me/roxybasicneedbot1
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@roxybasicneedbot
# Ask Doubt on telegram https://t.me/roxybasicneedbot1

import os
import re
import sys
import json
import time
import asyncio
import requests
import subprocess

import core as helper
from utils import progress_bar
from vars import API_ID, API_HASH, BOT_TOKEN
from aiohttp import ClientSession
from pyromod import listen
from subprocess import getstatusoutput

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ParseMode

bot = Client(
    "bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN)

WELCOME_IMAGE_PATH = "welcome.jpg"

# URL validation
def is_valid_url(url):
    url_pattern = re.compile(
        r'^https?://'
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'
        r'localhost|'
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
        r'(?::\d+)?'
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return url_pattern.match(url) is not None

def extract_url_from_line(line):
    line = line.strip()
    if not line:
        return None, None
    url_match = re.search(r'https?://[^\s]+', line)
    if url_match:
        url = url_match.group()
        title = line.replace(url, '').strip()
        if not title:
            title = f"File_{hash(url) % 1000}"
        return title, url
    if '.' in line and not line.startswith('/'):
        url = 'https://' + line
        if is_valid_url(url):
            return f"File_{hash(line) % 1000}", url
    return None, None

@bot.on_message(filters.command(["start"]))
async def start(bot: Client, m: Message):
    welcome_text = (
        f"<b>👋 Hello {m.from_user.mention}!</b>\n\n"
        f"<blockquote>📁 I download videos/PDFs from your <b>.TXT</b> file links.\n\n"
        f"🚀 Upload list → Bot downloads everything.</blockquote>"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Upload Files", callback_data="upload_files")],
        [
            InlineKeyboardButton("🔔 Channel", url="https://t.me/class_video_pdf"),
            InlineKeyboardButton("👨‍💻 Developer", url="https://t.me/class_video_pdf"),
        ]
    ])

    if os.path.exists(WELCOME_IMAGE_PATH):
        await m.reply_photo(WELCOME_IMAGE_PATH, caption=welcome_text, reply_markup=keyboard)
    else:
        await m.reply_text(welcome_text, reply_markup=keyboard)

@bot.on_callback_query()
async def callback_handler(bot: Client, query):
    if query.data == "upload_files":
        await query.answer("Send /upload to start!", show_alert=True)
    else:
        await query.answer()

@bot.on_message(filters.command(["upload"]))
async def upload(bot: Client, m: Message):
    editable = await m.reply_text('📤 Send your TXT file with links')
    input: Message = await bot.listen(editable.chat.id)
    x = await input.download()
    await input.delete()

    try:
        with open(x, "r", encoding='utf-8', errors='ignore') as f:
            lines = f.read().split("\n")

        links = []
        for line in lines:
            title, url = extract_url_from_line(line)
            if title and url:
                links.append([title, url])

        os.remove(x)

        if not links:
            await editable.edit("❌ No valid links found!")
            return

    except Exception as e:
        await editable.edit(f"❌ Error reading file: {e}")
        return

    await editable.edit(f"📊 Total Links: {len(links)}\n\nSend starting number (default: 1)")
    input0 = await bot.listen(m.chat.id)
    start_num = input0.text
    await input0.delete()

    try:
        count = int(start_num)
    except:
        count = 1

    await editable.edit("📝 Send Batch Name:")
    input1 = await bot.listen(m.chat.id)
    batch = input1.text
    await input1.delete()

    await editable.edit("🎬 Video Quality (144–1080):")
    input2 = await bot.listen(m.chat.id)
    quality = input2.text
    await input2.delete()

    await editable.edit("💬 Caption text:")
    input3 = await bot.listen(m.chat.id)
    caption = input3.text
    await input3.delete()

    await editable.edit("🖼 Thumbnail URL or 'no':")
    input4 = await bot.listen(m.chat.id)
    thumb = input4.text
    await input4.delete()

    if thumb.startswith("http"):
        getstatusoutput(f"wget '{thumb}' -O 'thumb.jpg'")
        if os.path.exists("thumb.jpg"):
            thumb = "thumb.jpg"
        else:
            thumb = "no"
    else:
        thumb = "no"

    await editable.delete()

    success = 0
    failed = 0

    for i in range(count - 1, len(links)):
        title, url = links[i]

        clean_title = re.sub(r'[<>:"/\\|?*]', '', title)[:60]
        fname = f"{str(i+1).zfill(3)}) {clean_title}"

        # Google Drive
        if "drive.google.com" in url:
            url = url.replace("file/d/", "uc?export=download&id=").replace("/view?usp=sharing","")

        # VisionIAS extractor
        if "visionias" in url:
            try:
                async with ClientSession() as session:
                    async with session.get(url, headers={"User-Agent":"Mozilla"}) as resp:
                        text = await resp.text()
                        m_match = re.search(r"(https://.*?playlist.*?m3u8.*?)\"", text)
                        if m_match:
                            url = m_match.group(1)
            except:
                pass

        # -------------------------------
        # SPECIAL FIX → AKAMAI + ALL m3u8
        # -------------------------------
        headers = (
            f'--add-header "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)" '
            f'--add-header "Accept: */*" '
            f'--add-header "Origin: https://{url.split("/")[2]}" '
            f'--add-header "Referer: {url}" '
            f'--add-header "Connection: keep-alive" '
        )

        # PDF
        if url.endswith(".pdf"):
            cmd = f'yt-dlp {headers} -o "{fname}.pdf" "{url}"'
        # YouTube special quality
        elif "youtu" in url:
            ytq = f"b[height<={quality}][ext=mp4]/bv[height<={quality}]+ba"
            cmd = f'yt-dlp {headers} -f "{ytq}" "{url}" -o "{fname}.%(ext)s"'
        # Normal + Akamai m3u8 FULL FIX
        else:
            cmd = f'yt-dlp {headers} -f "best" "{url}" -o "{fname}.%(ext)s" --no-check-certificate'

        prog = await m.reply_text(f"⬇️ Downloading...\n\n📁 {clean_title}\n🔗 {url}")

        # run yt-dlp
        subprocess.run(cmd, shell=True)

        # check files
        found = None
        for ext in [".mp4", ".mkv", ".webm", ".avi", ".mov", ".pdf"]:
            if os.path.exists(f"{fname}{ext}"):
                found = f"{fname}{ext}"
                break

        if found:
            # PDF
            if found.endswith(".pdf"):
                await bot.send_document(m.chat.id, found, caption=caption)
                os.remove(found)
                success += 1
            else:
                # video
                await helper.send_vid(bot, m, caption, found, thumb, fname, prog)
                success += 1
        else:
            failed += 1
            await prog.edit(f"❌ Failed: {clean_title}")

        await prog.delete()


    await m.reply_text(
        f"🎉 DONE\n\n"
        f"✅ Successful: {success}\n"
        f"❌ Failed: {failed}\n"
        f"📦 Total: {success+failed}"
    )

if __name__ == "__main__":
    bot.run()
