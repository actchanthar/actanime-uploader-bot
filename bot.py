#!/usr/bin/env python3
"""
ACT Anime MM – Video Uploader Bot
Pyrogram + MTProto | Cloudflare R2

Features:
  - Upload video/document to R2
  - Folder system (cd / mkdir / folder)
  - List files & folders cleanly
  - Delete files
  - Clean filenames + URL-safe links
"""

import os
import re
import logging
import mimetypes
from pathlib import Path
from urllib.parse import quote

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
R2_BUCKET       = os.environ.get("R2_BUCKET_NAME", "act")
R2_PUBLIC_BASE  = os.environ.get("R2_PUBLIC_BASE_URL", "").rstrip("/")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("actanime-bot")

# Current upload folder (per-owner, simple in-memory)
current_folder: dict[int, str] = {}

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
    """Return a properly URL-encoded public link."""
    encoded = quote(key, safe="/")
    if R2_PUBLIC_BASE:
        return f"{R2_PUBLIC_BASE}/{encoded}"
    return f"{R2_ENDPOINT}/{R2_BUCKET}/{encoded}"


def upload_file(local_path: str, key: str, content_type: str) -> str:
    r2.upload_file(
        local_path, R2_BUCKET, key,
        ExtraArgs={"ContentType": content_type},
    )
    return public_url(key)


def clean_filename(name: str) -> str:
    """Make filename safer for URLs and R2 keys."""
    name = name.strip()
    # Replace problematic characters
    name = name.replace("[", "(").replace("]", ")")
    name = name.replace("{", "(").replace("}", ")")
    name = re.sub(r'[<>:"\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name)
    return name


def normalize_folder(path: str) -> str:
    """Normalize folder path: no leading slash, always trailing slash (or empty)."""
    path = path.strip().strip("/")
    if not path:
        return ""
    return path + "/"


def get_folder(user_id: int) -> str:
    return current_folder.get(user_id, "videos/")


def set_folder(user_id: int, path: str):
    current_folder[user_id] = normalize_folder(path)


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
    folder = get_folder(msg.from_user.id)
    await msg.reply(
        "👋 **ACT Anime MM – Video Uploader**\n\n"
        "Video / ဖိုင် ပေးပို့ပါ → R2 မှာ upload လုပ်ပြီး link ပြန်ပို့မည်\n\n"
        f"📂 Current folder: `{folder or '/'}`\n\n"
        "**Commands:**\n"
        "• /start – ဒီ message\n"
        "• /folder – လက်ရှိ folder ကြည့်\n"
        "• /cd `<path>` – folder ပြောင်း\n"
        "• /mkdir `<path>` – folder အသစ်\n"
        "• /list – files & folders ကြည့်\n"
        "• /del `<key>` – file ဖျက်\n"
        "• /debug – debug info"
    )


# /folder  (or /pwd)
@app.on_message(filters.command(["folder", "pwd"]) & owner_filter)
async def cmd_folder(_, msg: Message):
    folder = get_folder(msg.from_user.id)
    await msg.reply(f"📂 Current folder:\n`{folder or '/'}`")


# /cd
@app.on_message(filters.command("cd") & owner_filter)
async def cmd_cd(_, msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply(
            "Usage:\n"
            "• `/cd videos`\n"
            "• `/cd jav/2026`\n"
            "• `/cd /`  (root)\n"
            "• `/cd ..` (parent)"
        )
        return

    target = parts[1].strip()
    current = get_folder(msg.from_user.id)

    if target in ("/", "~", ""):
        new_path = ""
    elif target == "..":
        # Go up one level
        parts_path = current.rstrip("/").split("/")
        if len(parts_path) <= 1:
            new_path = ""
        else:
            new_path = "/".join(parts_path[:-1]) + "/"
    else:
        if target.startswith("/"):
            new_path = normalize_folder(target)
        else:
            new_path = normalize_folder(current + target)

    set_folder(msg.from_user.id, new_path)
    await msg.reply(f"📂 Changed to:\n`{get_folder(msg.from_user.id) or '/'}`")


# /mkdir
@app.on_message(filters.command("mkdir") & owner_filter)
async def cmd_mkdir(_, msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply("Usage: `/mkdir jav/2026`")
        return

    folder = normalize_folder(parts[1].strip())
    if not folder:
        await msg.reply("❌ Invalid folder name.")
        return

    # R2 has no real folders – create a placeholder object
    key = folder + ".keep"
    try:
        r2.put_object(Bucket=R2_BUCKET, Key=key, Body=b"")
        await msg.reply(f"✅ Folder created:\n`{folder}`")
    except Exception as e:
        await msg.reply(f"❌ Error: `{e}`")


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
        f"Current folder: `{get_folder(msg.from_user.id) or '/'}`",
        f"R2_ACCESS_KEY: `{R2_ACCESS_KEY[:5]}...` (len={len(R2_ACCESS_KEY)})" if R2_ACCESS_KEY else "R2_ACCESS_KEY: `None`",
        f"R2_SECRET_KEY: `{R2_SECRET_KEY[:5]}...` (len={len(R2_SECRET_KEY)})" if R2_SECRET_KEY else "R2_SECRET_KEY: `None`",
    ]
    status = await msg.reply("\n".join(lines))

    try:
        status = await status.edit(status.text + "\n\n⏳ Testing `list_buckets`...")
        buckets = r2.list_buckets()
        bucket_names = [b["Name"] for b in buckets.get("Buckets", [])]
        status = await status.edit(status.text + f"\n✅ `list_buckets` Succeeded! Buckets: {bucket_names}")
    except Exception as e:
        status = await status.edit(status.text + f"\n❌ `list_buckets` Failed:\n`{e}`\n`{traceback.format_exc()[-150:]}`")

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
    folder = get_folder(msg.from_user.id)

    try:
        # List with delimiter to show folders + files in current path
        kwargs = {"Bucket": R2_BUCKET, "MaxKeys": 100, "Delimiter": "/"}
        if folder:
            kwargs["Prefix"] = folder

        resp = r2.list_objects_v2(**kwargs)

        folders = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
        files = [obj for obj in resp.get("Contents", []) if not obj["Key"].endswith("/.keep")]

        if not folders and not files:
            await wait.edit(f"📂 `{folder or '/'}` is empty.")
            return

        lines = [f"📂 **{folder or '/'}**\n"]

        if folders:
            lines.append("**Folders:**")
            for f in folders:
                name = f[len(folder):] if folder else f
                lines.append(f"📁 `{name}`")
            lines.append("")

        if files:
            lines.append("**Files:**")
            for obj in files:
                name = obj["Key"][len(folder):] if folder else obj["Key"]
                size_mb = obj["Size"] / 1_048_576
                lines.append(f"📄 `{name}` — {size_mb:.1f} MB")

        await wait.edit("\n".join(lines))
    except Exception as e:
        await wait.edit(f"❌ Error: `{e}`")


# /del
@app.on_message(filters.command("del") & owner_filter)
async def cmd_delete(_, msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        await msg.reply(
            "Usage:\n"
            "• `/del filename.mp4`  (current folder)\n"
            "• `/del videos/filename.mp4`  (full path)"
        )
        return

    key = parts[1].strip()
    # If no slash, treat as relative to current folder
    if "/" not in key:
        key = get_folder(msg.from_user.id) + key

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

    raw_name = getattr(media, "file_name", None) or f"{media.file_unique_id}"
    mime = getattr(media, "mime_type", None) or "video/mp4"

    if not Path(raw_name).suffix:
        ext = mimetypes.guess_extension(mime) or ".mp4"
        raw_name = f"{media.file_unique_id}{ext}"

    file_name = clean_filename(raw_name)
    folder = get_folder(msg.from_user.id)
    r2_key = folder + file_name
    file_size_mb = (getattr(media, "file_size", 0) or 0) / 1_048_576

    status = await msg.reply(
        f"⬇️ **Downloading…**\n"
        f"📄 `{file_name}` ({file_size_mb:.1f} MB)\n"
        f"📂 `{folder or '/'}`"
    )

    try:
        local_path = await client.download_media(
            msg,
            file_name=str(BASE_DIR / "tmp" / file_name),
        )
    except Exception as e:
        await status.edit(f"❌ Download failed:\n`{e}`")
        return

    await status.edit(
        f"⬆️ **Uploading to R2…**\n"
        f"📄 `{file_name}` ({file_size_mb:.1f} MB)\n"
        f"📂 `{folder or '/'}`"
    )
    try:
        url = upload_file(str(local_path), r2_key, mime)
    except Exception as e:
        await status.edit(f"❌ R2 Upload failed:\n`{e}`")
        Path(local_path).unlink(missing_ok=True)
        return

    Path(local_path).unlink(missing_ok=True)

    await status.edit(
        f"✅ **Upload Complete!**\n\n"
        f"📄 File: `{file_name}`\n"
        f"📂 Folder: `{folder or '/'}`\n"
        f"📏 Size: {file_size_mb:.2f} MB\n"
        f"📦 Bucket: `{R2_BUCKET}`\n\n"
        f"🔗 **Link:**\n{url}"
    )
    log.info("Uploaded %s → %s", r2_key, url)


# Reject everyone else
@app.on_message(~owner_filter)
async def reject(_, msg: Message):
    await msg.reply("⛔ Private bot.")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    (BASE_DIR / "tmp").mkdir(exist_ok=True)
    log.info("Starting bot… Owner=%s Bucket=%s", OWNER_ID, R2_BUCKET)
    app.run()
