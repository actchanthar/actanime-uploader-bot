#!/usr/bin/env python3
"""
ACT Anime MM – Video Uploader Bot
Pyrogram + MTProto | Cloudflare R2

Usage (owner only):
  Send any video / document → bot downloads → uploads to R2 → sends URL back
"""

import os
import asyncio
import logging
import mimetypes
from pathlib import Path

import boto3
from botocore.config import Config
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message

# ── Load config ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / "config.env")

API_ID          = int(os.environ["API_ID"])
API_HASH        = os.environ["API_HASH"]
BOT_TOKEN       = os.environ["BOT_TOKEN"]
OWNER_ID        = int(os.environ["OWNER_ID"])

R2_ACCESS_KEY   = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_KEY   = os.environ["R2_SECRET_ACCESS_KEY"]
R2_ENDPOINT     = os.environ["R2_ENDPOINT"]
R2_BUCKET       = os.environ.get("R2_BUCKET_NAME", "actanimemm-videos")
R2_PUBLIC_BASE  = os.environ.get("R2_PUBLIC_BASE_URL", "").rstrip("/")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("actanime-bot")

# ── R2 client ─────────────────────────────────────────────────────────────────
r2 = boto3.client(
    service_name="s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    region_name="auto",
    config=Config(signature_version="s3v4"),
)


def public_url(key: str) -> str:
    if R2_PUBLIC_BASE:
        return f"{R2_PUBLIC_BASE}/{key}"
    return f"{R2_ENDPOINT}/{R2_BUCKET}/{key}"


def upload_file(local_path: str, key: str, content_type: str) -> str:
    r2.upload_file(
        local_path, R2_BUCKET, key,
        ExtraArgs={"ContentType": content_type},
    )
    return public_url(key)


# ── Pyrogram bot ──────────────────────────────────────────────────────────────
app = Client(
    "actanime_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

owner_filter = filters.user(OWNER_ID)


# /start
@app.on_message(filters.command("start") & owner_filter)
async def cmd_start(_, msg: Message):
    await msg.reply(
        "👋 **ACT Anime MM – Video Uploader**\n\n"
        "Video / ဖိုင် တစ်ခု ပေးပို့ပါ → R2 မှာ upload လုပ်ပြီး link ပြန်ပို့ပေးမည်\n\n"
        "**Commands:**\n"
        "• /start – ဒီ message\n"
        "• /list  – R2 ထဲ files ကြည့်\n"
        "• /del `<key>` – file ဖျက်\n"
        "• /debug – debug အချက်အလက်များ ကြည့်မည်"
    )


# /debug
@app.on_message(filters.command("debug") & owner_filter)
async def cmd_debug(_, msg: Message):
    import traceback
    lines = [
        "🔍 **Debug Information:**",
        f"API_ID: `{API_ID}`",
        f"OWNER_ID: `{OWNER_ID}`",
        f"R2_ENDPOINT: `{R2_ENDPOINT}`",
        f"R2_BUCKET: `{R2_BUCKET}`",
        f"R2_ACCESS_KEY: `{R2_ACCESS_KEY[:5]}...` (len={len(R2_ACCESS_KEY)})" if R2_ACCESS_KEY else "R2_ACCESS_KEY: `None`",
        f"R2_SECRET_KEY: `{R2_SECRET_KEY[:5]}...` (len={len(R2_SECRET_KEY)})" if R2_SECRET_KEY else "R2_SECRET_KEY: `None`",
    ]
    status = await msg.reply("\n".join(lines))
    
    try:
        status = await status.edit(status.text + "\n\n⏳ Testing `list_buckets`...")
        buckets = r2.list_buckets()
        bucket_names = [b['Name'] for b in buckets.get('Buckets', [])]
        status = await status.edit(status.text + f"\n✅ `list_buckets` Succeeded! Buckets: {bucket_names}")
    except Exception as e:
        status = await status.edit(status.text + f"\n❌ `list_buckets` Failed:\n`{e}`\nTraceback:\n`{traceback.format_exc()[-200:]}`")
        
    try:
        status = await status.edit(status.text + f"\n\n⏳ Testing `list_objects` on bucket `{R2_BUCKET}`...")
        r2.list_objects_v2(Bucket=R2_BUCKET, MaxKeys=5)
        status = await status.edit(status.text + "\n✅ `list_objects` Succeeded!")
    except Exception as e:
        status = await status.edit(status.text + f"\n❌ `list_objects` Failed:\n`{e}`")


# /list
@app.on_message(filters.command("list") & owner_filter)
async def cmd_list(_, msg: Message):
    wait = await msg.reply("⏳ Loading…")
    try:
        resp = r2.list_objects_v2(Bucket=R2_BUCKET, MaxKeys=50)
        items = resp.get("Contents", [])
        if not items:
            await wait.edit("📂 Bucket is empty.")
            return
        lines = [f"📦 **{R2_BUCKET}** ({len(items)} files)\n"]
        for obj in items:
            size_mb = obj["Size"] / 1_048_576
            lines.append(f"• `{obj['Key']}` — {size_mb:.1f} MB")
        await wait.edit("\n".join(lines))
    except Exception as e:
        await wait.edit(f"❌ Error: `{e}`")


# /del
@app.on_message(filters.command("del") & owner_filter)
async def cmd_delete(_, msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply("Usage: `/del videos/filename.mp4`")
        return
    key = parts[1].strip()
    try:
        r2.delete_object(Bucket=R2_BUCKET, Key=key)
        await msg.reply(f"🗑️ Deleted:\n`{key}`")
    except Exception as e:
        await msg.reply(f"❌ Error: `{e}`")


# Video / Document handler
@app.on_message((filters.video | filters.document | filters.animation) & owner_filter)
async def handle_upload(client: Client, msg: Message):
    media = msg.video or msg.document or msg.animation
    if not media:
        return

    # File name & content type
    file_name = getattr(media, "file_name", None) or f"{media.file_unique_id}"
    mime = getattr(media, "mime_type", None) or "video/mp4"
    if "." not in Path(file_name).suffix:
        ext = mimetypes.guess_extension(mime) or ".mp4"
        file_name = f"{media.file_unique_id}{ext}"

    r2_key = f"videos/{file_name}"
    file_size_mb = (getattr(media, "file_size", 0) or 0) / 1_048_576

    status = await msg.reply(
        f"⬇️ **Downloading…**\n"
        f"📄 `{file_name}` ({file_size_mb:.1f} MB)"
    )

    # Download from Telegram
    try:
        local_path = await client.download_media(
            msg,
            file_name=str(BASE_DIR / "tmp" / file_name),
        )
    except Exception as e:
        await status.edit(f"❌ Download failed:\n`{e}`")
        return

    # Upload to R2
    await status.edit(
        f"⬆️ **Uploading to R2…**\n"
        f"📄 `{file_name}` ({file_size_mb:.1f} MB)"
    )
    try:
        url = upload_file(str(local_path), r2_key, mime)
    except Exception as e:
        await status.edit(f"❌ R2 Upload failed:\n`{e}`")
        Path(local_path).unlink(missing_ok=True)
        return

    # Clean up local tmp file
    Path(local_path).unlink(missing_ok=True)

    # Done – send result
    await status.edit(
        f"✅ **Upload Complete!**\n\n"
        f"📄 File: `{file_name}`\n"
        f"📏 Size: {file_size_mb:.2f} MB\n"
        f"📦 Bucket: `{R2_BUCKET}`\n\n"
        f"🔗 **Link:**\n`{url}`"
    )
    log.info("Uploaded %s → %s", file_name, url)


# Reject everyone else
@app.on_message(~owner_filter)
async def reject(_, msg: Message):
    await msg.reply("⛔ Private bot.")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Create tmp folder for downloads
    (BASE_DIR / "tmp").mkdir(exist_ok=True)
    log.info("Starting bot… Owner=%s Bucket=%s", OWNER_ID, R2_BUCKET)
    app.run()
