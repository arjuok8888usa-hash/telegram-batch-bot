```python
#!/usr/bin/env python3
"""
Authorized Course Batch Exporter & TXT Parser Telegram Bot
==========================================================
A Telegram bot built with `python-telegram-bot` (v20+ async) and `httpx` for:
1. Exporting course batch schedules and resource links (.txt / .json) from an
   authorized LMS REST API that you own or have permission to access.
2. Parsing and organizing uploaded `.txt` batch manifest files (`Title: URL`)
   into categorized lecture and PDF study material lists.
3. Running a lightweight HTTP health-check server on `$PORT` so it deploys
   cleanly on free cloud Web Service tiers (such as Render or Koyeb).

NOTE: This bot is designed strictly for authorized LMS endpoints and user-owned
educational content. Do not use it to scrape or bypass DRM on third-party platforms.

Setup:
    pip install -r requirements.txt

Environment Variables:
    TELEGRAM_BOT_TOKEN : Bot token from @BotFather on Telegram
    LMS_API_BASE_URL   : Base URL of your authorized course API (optional, defaults to demo-ready URL)
    PORT               : Port for the cloud health-check server (automatically set by Render)
"""

import io
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Environment configuration
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
LMS_API_BASE_URL = os.getenv("LMS_API_BASE_URL", "https://api.your-lms-domain.com/v1")
PORT = int(os.getenv("PORT", "8080"))

# Conversation states for authorized API export flow
WAITING_FOR_API_TOKEN, WAITING_FOR_BATCH_ID = range(2)


# =====================================================================
# Lightweight Health-Check Server for Render Free Web Service Tier
# =====================================================================


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler so Render's free Web Service tier detects an open port."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Course Batch Telegram Bot is running.")

    def log_message(self, format: str, *args) -> None:
        # Suppress noisy HTTP health-check access logs
        return


def start_health_server() -> None:
    """Start the HTTP server in a background daemon thread."""
    server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
    logger.info("Health-check server listening on port %d", PORT)
    server.serve_forever()


# =====================================================================
# Data Models & Parsing Helpers
# =====================================================================


@dataclass
class BatchResource:
    """Represents a single course item (video lecture, PDF note, or link)."""

    title: str
    url: str
    category: str  # "video", "pdf", or "other"


def classify_url(url: str) -> str:
    """Classify a resource URL based on extension or common media patterns."""
    lower_url = url.lower()
    if any(
        ext in lower_url
        for ext in (".mp4", ".m3u8", ".mkv", ".webm", "youtube.com", "youtu.be")
    ):
        return "video"
    if ".pdf" in lower_url or "/pdf/" in lower_url:
        return "pdf"
    return "other"


def parse_txt_manifest(content: str) -> List[BatchResource]:
    """
    Parse a standard `Title: URL` or `Title - URL` text file into BatchResource items.
    Lines starting with `#` or without a valid HTTP(S) URL are ignored.
    """
    resources: List[BatchResource] = []
    line_pattern = re.compile(r"^(.*?)\s*[:\-|]\s*(https?://\S+)\s*$")

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        match = line_pattern.match(line)
        if match:
            title = match.group(1).strip() or "Untitled Resource"
            url = match.group(2).strip()
            resources.append(
                BatchResource(title=title, url=url, category=classify_url(url))
            )
        elif line.startswith("http://") or line.startswith("https://"):
            resources.append(
                BatchResource(
                    title="Resource Link", url=line, category=classify_url(line)
                )
            )

    return resources


def format_resources_as_txt(batch_name: str, resources: List[BatchResource]) -> str:
    """Format a list of BatchResource items into a clean, downloadable .txt file."""
    lines = [
        f"# Batch Manifest: {batch_name}",
        f"# Total Items: {len(resources)}",
        "",
    ]
    for idx, item in enumerate(resources, start=1):
        tag = item.category.upper()
        lines.append(f"{idx:03d}. [{tag}] {item.title} : {item.url}")
    return "\n".join(lines)


# =====================================================================
# Telegram Command Handlers
# =====================================================================


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome instructions and available commands."""
    welcome_text = (
        "<b>Welcome to the Course Batch Manager & TXT Exporter Bot</b>\n\n"
        "You can use this bot to manage authorized educational course batches:\n"
        "• <b>/export</b> — Connect to your authorized LMS API token and export a batch manifest as a <code>.txt</code> file.\n"
        "• <b>/sample</b> — Generate a sample <code>.txt</code> batch manifest template.\n"
        "• <b>Upload any <code>.txt</code> file</b> — Send a <code>.txt</code> file formatted as <code>Title : URL</code> to filter video lectures and PDF notes.\n"
        "• <b>/cancel</b> — Cancel the current operation."
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)


async def sample_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a sample batch manifest .txt file so users can see the expected format."""
    sample_items = [
        BatchResource(
            "01. Introduction & Syllabus Overview",
            "https://example.com/lectures/intro.mp4",
            "video",
        ),
        BatchResource(
            "01. Syllabus & Study Plan Notes",
            "https://example.com/notes/syllabus.pdf",
            "pdf",
        ),
        BatchResource(
            "02. Core Concepts - Session 1",
            "https://example.com/lectures/session1.m3u8",
            "video",
        ),
        BatchResource(
            "02. Practice Worksheet PDF",
            "https://example.com/notes/worksheet1.pdf",
            "pdf",
        ),
    ]
    txt_output = format_resources_as_txt("Sample_Foundation_Batch_2026", sample_items)
    buffer = io.BytesIO(txt_output.encode("utf-8"))
    buffer.name = "Sample_Foundation_Batch_2026.txt"

    await update.message.reply_document(
        document=buffer,
        caption="Here is a sample batch `.txt` manifest. You can forward or upload a `.txt` file like this to the bot anytime to filter its videos and PDFs.",
    )


# =====================================================================
# Authorized LMS API Batch Export Conversation Flow
# =====================================================================


async def export_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Begin the authorized API batch export flow."""
    await update.message.reply_text(
        "<b>Step 1 of 2:</b> Please send your authorized LMS API Bearer Token "
        "(or type <code>demo</code> to test with sample course data).\n\n"
        "Send /cancel at any time to stop.",
        parse_mode=ParseMode.HTML,
    )
    return WAITING_FOR_API_TOKEN


async def receive_api_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate token and list available batches from the authorized API."""
    api_token = update.message.text.strip()
    context.user_data["api_token"] = api_token

    if api_token.lower() == "demo":
        batches = [
            {"id": "batch_101", "name": "Foundation General Studies 2026"},
            {"id": "batch_102", "name": "Quantitative Aptitude Practice Batch"},
            {"id": "batch_103", "name": "Complete Computer Awareness Course"},
        ]
    else:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{LMS_API_BASE_URL.rstrip('/')}/batches",
                    headers={"Authorization": f"Bearer {api_token}"},
                )
                response.raise_for_status()
                data = response.json()
                batches = data.get("batches", [])
        except httpx.HTTPError as exc:
            logger.warning("API request failed: %s", exc)
            await update.message.reply_text(
                f"Could not fetch batches from `{LMS_API_BASE_URL}`.\n"
                "Please verify your API token and endpoint, or type `/export` and use `demo` to test.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return ConversationHandler.END

    if not batches:
        await update.message.reply_text("No active batches were found for this account.")
        return ConversationHandler.END

    batch_lines = [f"• <code>{b['id']}</code> — {b['name']}" for b in batches]
    await update.message.reply_text(
        "<b>Step 2 of 2: Available Batches</b>\n\n"
        + "\n".join(batch_lines)
        + "\n\nReply with the <b>Batch ID</b> (e.g. <code>batch_101</code>) to export its manifest as a <code>.txt</code> file:",
        parse_mode=ParseMode.HTML,
    )
    return WAITING_FOR_BATCH_ID


async def receive_batch_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Fetch batch contents and return a formatted .txt file."""
    batch_id = update.message.text.strip()
    api_token = context.user_data.get("api_token", "demo")

    if api_token.lower() == "demo":
        batch_title = f"Demo_Batch_{batch_id}"
        resources = [
            BatchResource(
                "Lecture 01 - Orientation",
                "https://cdn.example.com/courses/lec01.mp4",
                "video",
            ),
            BatchResource(
                "Lecture 01 - Class Notes",
                "https://cdn.example.com/courses/notes01.pdf",
                "pdf",
            ),
            BatchResource(
                "Lecture 02 - Core Module",
                "https://cdn.example.com/courses/lec02.mp4",
                "video",
            ),
            BatchResource(
                "Lecture 02 - Practice Set",
                "https://cdn.example.com/courses/practice02.pdf",
                "pdf",
            ),
        ]
    else:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(
                    f"{LMS_API_BASE_URL.rstrip('/')}/batches/{batch_id}/contents",
                    headers={"Authorization": f"Bearer {api_token}"},
                )
                response.raise_for_status()
                payload = response.json()
                batch_title = payload.get("batch_name", batch_id)
                raw_items = payload.get("items", [])
                resources = [
                    BatchResource(
                        title=item.get("title", "Untitled"),
                        url=item.get("url", ""),
                        category=classify_url(item.get("url", "")),
                    )
                    for item in raw_items
                    if item.get("url")
                ]
        except httpx.HTTPError as exc:
            logger.warning("Batch content fetch failed: %s", exc)
            await update.message.reply_text(
                f"Failed to retrieve contents for batch `{batch_id}`. Please check the ID and try again.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return ConversationHandler.END

    txt_content = format_resources_as_txt(batch_title, resources)
    safe_filename = re.sub(r"[^A-Za-z0-9_\-]", "_", batch_title) + ".txt"
    doc_buffer = io.BytesIO(txt_content.encode("utf-8"))
    doc_buffer.name = safe_filename

    videos_count = sum(1 for r in resources if r.category == "video")
    pdfs_count = sum(1 for r in resources if r.category == "pdf")

    await update.message.reply_document(
        document=doc_buffer,
        caption=(
            f"<b>Batch Export Complete:</b> {batch_title}\n"
            f"• Total items: {len(resources)}\n"
            f"• Video lectures: {videos_count}\n"
            f"• PDF notes: {pdfs_count}"
        ),
        parse_mode=ParseMode.HTML,
    )
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel active conversation state."""
    context.user_data.clear()
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END


# =====================================================================
# Uploaded .TXT Batch File Parser & Filter Handler
# =====================================================================


async def handle_uploaded_txt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parse an uploaded .txt batch file and offer interactive filtering."""
    document = update.message.document
    if not document or not document.file_name.lower().endswith(".txt"):
        await update.message.reply_text("Please upload a valid `.txt` batch manifest file.")
        return

    tg_file = await document.get_file()
    raw_bytes = await tg_file.download_as_bytearray()
    content = raw_bytes.decode("utf-8", errors="replace")

    resources = parse_txt_manifest(content)
    if not resources:
        await update.message.reply_text(
            "No valid `Title : URL` entries were found in the uploaded `.txt` file."
        )
        return

    context.user_data["parsed_resources"] = resources
    context.user_data["parsed_filename"] = os.path.splitext(document.file_name)[0]

    videos = [r for r in resources if r.category == "video"]
    pdfs = [r for r in resources if r.category == "pdf"]
    others = [r for r in resources if r.category == "other"]

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Videos Only ({len(videos)})", callback_data="filter_video"
                ),
                InlineKeyboardButton(
                    f"PDFs Only ({len(pdfs)})", callback_data="filter_pdf"
                ),
            ],
            [
                InlineKeyboardButton(
                    f"Clean Full TXT ({len(resources)})", callback_data="filter_all"
                ),
                InlineKeyboardButton("Export JSON", callback_data="filter_json"),
            ],
        ]
    )

    summary = (
        f"<b>Parsed Batch File:</b> <code>{document.file_name}</code>\n\n"
        f"• <b>Total Links:</b> {len(resources)}\n"
        f"• <b>Videos:</b> {len(videos)}\n"
        f"• <b>PDF Notes:</b> {len(pdfs)}\n"
        f"• <b>Other Links:</b> {len(others)}\n\n"
        "Select an export format below:"
    )
    await update.message.reply_text(
        summary, parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


async def handle_filter_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle inline button presses for exporting filtered .txt or .json files."""
    query = update.callback_query
    await query.answer()

    resources: List[BatchResource] = context.user_data.get("parsed_resources", [])
    base_name: str = context.user_data.get("parsed_filename", "batch_export")

    if not resources:
        await query.edit_message_text(
            "Session expired. Please upload the `.txt` file again."
        )
        return

    action = query.data
    if action == "filter_video":
        filtered = [r for r in resources if r.category == "video"]
        out_text = format_resources_as_txt(f"{base_name}_Videos", filtered)
        out_name = f"{base_name}_videos.txt"
    elif action == "filter_pdf":
        filtered = [r for r in resources if r.category == "pdf"]
        out_text = format_resources_as_txt(f"{base_name}_PDFs", filtered)
        out_name = f"{base_name}_pdfs.txt"
    elif action == "filter_json":
        payload = [
            {"title": r.title, "url": r.url, "category": r.category}
            for r in resources
        ]
        out_text = json.dumps(payload, indent=2, ensure_ascii=False)
        out_name = f"{base_name}.json"
    else:
        out_text = format_resources_as_txt(base_name, resources)
        out_name = f"{base_name}_clean.txt"

    buf = io.BytesIO(out_text.encode("utf-8"))
    buf.name = out_name
    await query.message.reply_document(
        document=buf,
        caption=f"Generated `{out_name}`.",
        parse_mode=ParseMode.MARKDOWN,
    )


def main() -> None:
    """Start the health-check server and Telegram bot polling."""
    if BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        logger.warning(
            "TELEGRAM_BOT_TOKEN is not set. Set the TELEGRAM_BOT_TOKEN environment variable on Render."
        )

    # Start background HTTP server so Render's Free Web Service tier sees an open port
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()

    app = Application.builder().token(BOT_TOKEN).build()

    export_conv = ConversationHandler(
        entry_points=[CommandHandler("export", export_start)],
        states={
            WAITING_FOR_API_TOKEN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_token)
            ],
            WAITING_FOR_BATCH_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_batch_id)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("sample", sample_command))
    app.add_handler(export_conv)
    app.add_handler(MessageHandler(filters.Document.TXT, handle_uploaded_txt))
    app.add_handler(CallbackQueryHandler(handle_filter_callback, pattern=r"^filter_"))

    logger.info("Bot is polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
```
