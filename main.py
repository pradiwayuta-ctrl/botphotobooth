import os
import time
import io
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from PIL import Image, ImageOps, ImageEnhance, ImageDraw, ImageFont
import aiosqlite

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters
)

# ==========================================
# LOGGING & KONFIGURASI BOT CC PUNKSHIT
# ==========================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("punkshit_photobooth")

TOKEN = os.getenv("TELEGRAM_PHOTOBOOTH_BOT_TOKEN")
if not TOKEN:
    logger.warning("TELEGRAM_PHOTOBOOTH_BOT_TOKEN belum diset di Environment Variable!")

CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@CCPunkshitRP")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", BASE_DIR)
DB_NAME = os.path.join(DB_DIR, "punkshit_photobooth.db")

# Konfigurasi 3 Frame berdasarkan desain gambar
FRAME_CONFIG = {
    1: {
        "name": "⚡ Frame 1: Punkshit Doodle & Guitar",
        "file": "frame1.png",
        "preview": "preview_frame1.jpg"
    },
    2: {
        "name": "🏁 Frame 2: Punkshit Minimalist 2026",
        "file": "frame2.png",
        "preview": "preview_frame2.jpg"
    },
    3: {
        "name": "☠️ Frame 3: Punkshit Halftone Crew",
        "file": "frame3.png",
        "preview": "preview_frame3.jpg"
    }
}

# Opsi Preset Ucapan Instan (Button)
PRESET_MESSAGES = {
    "msg_p1": "Stay Punk & Solid! ⚡",
    "msg_p2": "Punk's Not Dead! ☠️",
    "msg_p3": "Huru-Hara CC Punkshit! 🎸",
    "msg_p4": "Oi! Oi! Oi! 🔥"
}

# ==========================================
# DATABASE HELPER
# ==========================================
@asynccontextmanager
async def get_db_connection():
    db = await aiosqlite.connect(DB_NAME, timeout=30.0)
    try:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        yield db
    finally:
        await db.close()

async def init_db():
    os.makedirs(os.path.dirname(DB_NAME), exist_ok=True)
    async with get_db_connection() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS photobooth_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                frame_selected INTEGER DEFAULT 1,
                filter_selected TEXT DEFAULT 'normal',
                pesan TEXT,
                file_id TEXT,
                channel_msg_id INTEGER,
                created_at INTEGER
            )
        """)
        await db.commit()

async def post_init(application):
    await init_db()

# ==========================================
# EFEK FILTER & WATERMARK PROCESSING (PIL)
# ==========================================
def apply_image_filter(img: Image.Image, filter_type: str) -> Image.Image:
    """Menerapkan filter visual pada foto user."""
    if filter_type == "bw":
        gray = ImageOps.grayscale(img).convert("RGBA")
        enhancer = ImageEnhance.Contrast(gray)
        return enhancer.enhance(1.7)
    
    elif filter_type == "red_acid":
        gray = ImageOps.grayscale(img)
        red_tint = ImageOps.colorize(gray, black="#050505", white="#ff2222").convert("RGBA")
        enhancer = ImageEnhance.Contrast(red_tint)
        return enhancer.enhance(1.4)
    
    elif filter_type == "vintage":
        rgb_img = img.convert("RGB") if img.mode != "RGB" else img
        r, g, b = rgb_img.split()
        r = r.point(lambda i: min(255, int(i * 1.15)))
        b = b.point(lambda i: int(i * 0.82))
        vintage_img = Image.merge("RGB", (r, g, b)).convert("RGBA")
        enhancer = ImageEnhance.Contrast(vintage_img)
        return enhancer.enhance(1.2)
        
    return img

def draw_watermark(img: Image.Image, username: str) -> Image.Image:
    """Menempelkan Watermark Username & Event Tag di Pojok Foto."""
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    
    banner_height = max(28, int(h * 0.035))
    ov_draw.rectangle([0, h - banner_height, w, h], fill=(10, 10, 10, 190))
    img = Image.alpha_composite(img, overlay)
    
    draw = ImageDraw.Draw(img)
    wm_text = f"📷 @{username}  •  CC PUNKSHIT EVENT  •  {datetime.now().strftime('%Y-%m-%d')}"
    
    try:
        font = ImageFont.truetype("arial.ttf", int(banner_height * 0.45))
    except Exception:
        font = ImageFont.load_default()
        
    draw.text((int(w * 0.02), h - int(banner_height * 0.75)), wm_text, fill=(255, 255, 255, 230), font=font)
    return img

def process_photobooth_image(user_bytes: bytes, frame_num: int, filter_type: str, username: str) -> io.BytesIO:
    """Pipeline pemrosesan gabungan: Filter -> Resize -> Frame Overlay -> Watermark."""
    user_img = Image.open(io.BytesIO(user_bytes)).convert("RGBA")
    
    user_img = apply_image_filter(user_img, filter_type)
    
    frame_info = FRAME_CONFIG.get(frame_num, FRAME_CONFIG[1])
    frame_path = os.path.join(BASE_DIR, frame_info["file"])

    if os.path.exists(frame_path):
        frame_img = Image.open(frame_path).convert("RGBA")
        user_img_resized = user_img.resize(frame_img.size, Image.Resampling.LANCZOS)
        combined = Image.alpha_composite(user_img_resized, frame_img)
    else:
        w, h = user_img.size
        border = max(24, int(min(w, h) * 0.04))
        colors = {1: (20, 20, 20, 255), 2: (235, 230, 225, 255), 3: (160, 20, 20, 255)}
        bg_color = colors.get(frame_num, (20, 20, 20, 255))
        combined = Image.new("RGBA", (w + (border * 2), h + (border * 2) + 60), bg_color)
        combined.paste(user_img, (border, border), user_img if user_img.mode == 'RGBA' else None)

    combined = draw_watermark(combined, username)

    output = io.BytesIO()
    combined.convert("RGB").save(output, format="JPEG", quality=95)
    output.seek(0)
    return output

# ==========================================
# KEYBOARD BUILDERS
# ==========================================
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Ambil Foto (Pilih Frame)", callback_data="pb_select_frame")],
        [InlineKeyboardButton("🖼️ Wall of Fame / Galeri", callback_data="pb_gallery"), InlineKeyboardButton("📜 Info Staff", callback_data="pb_info")]
    ])

def frame_selection_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Frame 1: Punkshit Doodle & Guitar", callback_data="pb_frame_1")],
        [InlineKeyboardButton("🏁 Frame 2: Punkshit Minimalist 2026", callback_data="pb_frame_2")],
        [InlineKeyboardButton("☠️ Frame 3: Punkshit Halftone Crew", callback_data="pb_frame_3")],
        [InlineKeyboardButton("◀️ Kembali ke Menu Utama", callback_data="pb_main")]
    ])

def filter_selection_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📷 Normal / Asli", callback_data="pb_filter_normal"),
            InlineKeyboardButton("⬛ B&W Grunge", callback_data="pb_filter_bw")
        ],
        [
            InlineKeyboardButton("🔴 Red Acid", callback_data="pb_filter_red_acid"),
            InlineKeyboardButton("📼 Vintage Sepia", callback_data="pb_filter_vintage")
        ],
        [InlineKeyboardButton("❌ Batal", callback_data="pb_main")]
    ])

def message_preset_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ Stay Punk & Solid!", callback_data="pb_msg_p1"),
            InlineKeyboardButton("☠️ Punk's Not Dead!", callback_data="pb_msg_p2")
        ],
        [
            InlineKeyboardButton("🎸 Huru-Hara Punkshit!", callback_data="pb_msg_p3"),
            InlineKeyboardButton("🔥 Oi! Oi! Oi!", callback_data="pb_msg_p4")
        ],
        [
            InlineKeyboardButton("✏️ Ketik Pesan Custom", callback_data="pb_msg_custom"),
            InlineKeyboardButton("➡️ Tanpa Pesan", callback_data="pb_msg_none")
        ],
        [InlineKeyboardButton("❌ Batal / Reset", callback_data="pb_main")]
    ])

def btn_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali ke Menu Utama", callback_data="pb_main")]])

# ==========================================
# HANDLERS & ROUTER
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    text = (
        "⚡ <b>PHOTOBOOTH SERVICE — CC PUNKSHIT</b> ⚡\n"
        "──────────────────────────────\n"
        "<i>\"Kamera & Frame Siap — Zero Typing, Serba Klik!\"</i>\n\n"
        "Selamat datang! Staf Photobooth siap bantu cetak foto fotbar kamu.\n\n"
        "<b>Fitur Utama:</b>\n"
        "• <b>3 Pilihan Frame PNG</b> (Doodle, Minimalist, Halftone)\n"
        "• <b>Efek Filter:</b> Normal, B&W Grunge, Red Acid & Vintage\n"
        "• <b>Watermark Otomatis:</b> Tag @username + Tanggal Event\n"
        "• Auto-Broadcast ke <b>@CCPunkshitRP</b>\n\n"
        "Silakan tekan tombol di bawah:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_menu_keyboard(), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=main_menu_keyboard(), parse_mode="HTML")

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "pb_main":
        await start(update, context)

    elif data == "pb_select_frame":
        text = (
            "🖼️ <b>PILIH DESIGN FRAME PHOTOBOOTH</b>\n"
            "──────────────────────────────\n"
            "Pilih salah satu dari 3 desain frame PNG khusus CC Punkshit:"
        )
        if query.message.photo:
            await query.message.delete()
            await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=frame_selection_keyboard(), parse_mode="HTML")
        else:
            await query.edit_message_text(text, reply_markup=frame_selection_keyboard(), parse_mode="HTML")

    elif data.startswith("pb_frame_"):
        frame_num = int(data.split("_")[2])
        frame_info = FRAME_CONFIG.get(frame_num, FRAME_CONFIG[1])
        
        context.user_data['selected_frame'] = frame_num
        context.user_data['state'] = "WAITING_PHOTO"

        frame_name = frame_info["name"]
        preview_path = os.path.join(BASE_DIR, frame_info["preview"])

        caption = (
            f"🖼️ <b>PREVIEW: {frame_name}</b>\n"
            "──────────────────────────────\n"
            "📷 <b>LANGKAH 1 DARI 3:</b>\n"
            "Upload <b>FOTO FOTBAR</b> kamu ke chat ini sekarang!"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Ganti Frame", callback_data="pb_select_frame")],
            [InlineKeyboardButton("❌ Batal", callback_data="pb_main")]
        ])

        await query.message.delete()

        if os.path.exists(preview_path):
            with open(preview_path, 'rb') as photo:
                await context.bot.send_photo(chat_id=query.message.chat_id, photo=photo, caption=caption, reply_markup=keyboard, parse_mode="HTML")
        else:
            await context.bot.send_message(chat_id=query.message.chat_id, text=caption, reply_markup=keyboard, parse_mode="HTML")

    elif data.startswith("pb_filter_"):
        filter_type = data.replace("pb_filter_", "")
        context.user_data['selected_filter'] = filter_type
        context.user_data['state'] = "WAITING_MESSAGE_SELECTION"

        filter_names = {
            "normal": "📷 Normal / Asli",
            "bw": "⬛ B&W Grunge",
            "red_acid": "🔴 Red Acid",
            "vintage": "📼 Vintage Sepia"
        }
        
        text = (
            f"✅ <b>Filter Terpilih:</b> {filter_names.get(filter_type, 'Normal')}\n"
            "──────────────────────────────\n"
            "💬 <b>LANGKAH 3 DARI 3 — PILIH UCAPAN:</b>\n"
            "Klik ucapan instan di bawah ATAU ketik ucapan custom sendiri:"
        )
        await query.edit_message_text(text, reply_markup=message_preset_keyboard(), parse_mode="HTML")

    elif data.startswith("pb_msg_"):
        msg_key = data.replace("pb_msg_", "")
        
        if msg_key == "custom":
            context.user_data['state'] = "WAITING_CUSTOM_MESSAGE"
            await query.edit_message_text(
                "✏️ <b>KETIK PESAN CUSTOM</b>\n"
                "──────────────────────────────\n"
                "Ketik pesan / quote tongkrongan kamu di kolom chat sekarang:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal", callback_data="pb_main")]]),
                parse_mode="HTML"
            )
        else:
            if msg_key == "none":
                selected_text = "Salam Hangat dari Photobooth Event! ✨"
            else:
                selected_text = PRESET_MESSAGES.get(f"msg_{msg_key}", "Stay Punk! ⚡")

            await execute_photobooth_pipeline(update, context, text_message=selected_text)

    elif data == "pb_gallery":
        async with get_db_connection() as db:
            async with db.execute("SELECT username, frame_selected, filter_selected, pesan FROM photobooth_logs ORDER BY id DESC LIMIT 5") as cursor:
                rows = await cursor.fetchall()

        if not rows:
            gal_text = "<i>Belum ada foto yang terdaftar di galeri event.</i>"
        else:
            gal_text = ""
            for uname, fr, fl, msg in rows:
                fname = FRAME_CONFIG.get(fr, {}).get("name", "Standard")
                gal_text += f"• <b>@{uname}</b> [{fname} | {fl.upper()}]\n  💬 <i>\"{msg}\"</i>\n\n"

        text = f"🖼️ <b>WALL OF FAME / GALERI PHOTOBOOTH CC PUNKSHIT</b>\n\n{gal_text}"
        await query.edit_message_text(text, reply_markup=btn_back(), parse_mode="HTML")

    elif data == "pb_info":
        text = (
            "📜 <b>PANDUAN STAFF PHOTOBOOTH</b>\n"
            "──────────────────────────────\n"
            "1. Klik <b>Ambil Foto</b> & tentukan pilihan frame.\n"
            "2. Upload foto fotbar kamu.\n"
            "3. Pilih <b>Filter Visual</b> (Normal, B&W, Red Acid, Vintage).\n"
            "4. Pilih ucapan instan via tombol / ketik custom.\n"
            "5. Foto + Watermark otomatis dikirim ke <b>@CCPunkshitRP</b>."
        )
        await query.edit_message_text(text, reply_markup=btn_back(), parse_mode="HTML")

# ==========================================
# HANDLING UPLOAD FOTO & INPUT PESAN
# ==========================================
async def handle_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file_id = update.message.photo[-1].file_id
    context.user_data['photo_file_id'] = photo_file_id
    context.user_data['state'] = "WAITING_FILTER_SELECTION"

    frame_num = context.user_data.get('selected_frame', 1)
    frame_name = FRAME_CONFIG[frame_num]["name"]

    text = (
        f"📸 <b>FOTO FOTBAR TERIMA!</b>\n"
        f"🖼️ Frame Terpasang: <b>{frame_name}</b>\n"
        "──────────────────────────────\n"
        "🎨 <b>LANGKAH 2 DARI 3 — PILIH FILTER FOTO:</b>\n"
        "Pilih salah satu efek visual warna untuk foto kamu:"
    )
    await update.message.reply_text(text, reply_markup=filter_selection_keyboard(), parse_mode="HTML")

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    if state == "WAITING_CUSTOM_MESSAGE":
        user_text = update.message.text.strip()
        await execute_photobooth_pipeline(update, context, text_message=user_text)

# ==========================================
# PIPELINE EXECUTION & BROADCAST
# ==========================================
async def execute_photobooth_pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE, text_message: str):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    frame_num = context.user_data.get('selected_frame', 1)
    filter_type = context.user_data.get('selected_filter', 'normal')
    photo_file_id = context.user_data.get('photo_file_id')
    now = int(time.time())

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="upload_photo")

    try:
        photo_file = await context.bot.get_file(photo_file_id)
        photo_bytes = await photo_file.download_as_bytearray()
        
        framed_image_stream = process_photobooth_image(photo_bytes, frame_num, filter_type, username)

        frame_name = FRAME_CONFIG[frame_num]["name"]
        caption_text = (
            "⚡ <b>PHOTOBOOTH CC PUNKSHIT SNAPSHOT</b> ⚡\n"
            "──────────────────────────────\n"
            f"👤 <b>Fotbar  :</b> @{username}\n"
            f"🖼️ <b>Frame   :</b> {frame_name}\n"
            f"🎨 <b>Filter  :</b> {filter_type.upper()}\n"
            f"💬 <b>Pesan   :</b>\n<i>\"{text_message}\"</i>\n"
            "──────────────────────────────\n"
            "🏷️ <i>Watermarked & Published automatically</i>"
        )

        ch_msg_id = None
        if CHANNEL_ID:
            try:
                framed_image_stream.seek(0)
                ch_msg = await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=framed_image_stream,
                    caption=caption_text,
                    parse_mode="HTML"
                )
                ch_msg_id = ch_msg.message_id
            except Exception as e:
                logger.error(f"Gagal broadcast ke channel {CHANNEL_ID}: {e}")

        framed_image_stream.seek(0)
        target_msg = update.callback_query.message if update.callback_query else update.message
        await target_msg.reply_photo(
            photo=framed_image_stream,
            caption=(
                "✅ <b>FOTO BERHASIL DICETAK & DIPOS!</b>\n\n"
                f"Foto fotbar kamu dengan filter <b>{filter_type.upper()}</b> & watermark "
                f"berhasil dipublikasikan ke channel <b>{CHANNEL_ID}</b>."
            ),
            reply_markup=btn_back(),
            parse_mode="HTML"
        )

        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO photobooth_logs (user_id, username, frame_selected, filter_selected, pesan, file_id, channel_msg_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, username, frame_num, filter_type, text_message, photo_file_id, ch_msg_id, now)
            )
            await db.commit()

    except Exception as e:
        logger.error(f"Error photobooth pipeline: {e}")
        target_msg = update.callback_query.message if update.callback_query else update.message
        await target_msg.reply_text("⚠️ <b>Ada kendala teknis saat memproses foto.</b> Silakan coba lagi.", reply_markup=btn_back(), parse_mode="HTML")
    finally:
        context.user_data.clear()

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    asyncio.run(init_db())
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_router))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    print("⚡ Bot Photobooth (Filter + Watermark + Zero Typing) Running...")
    app.run_polling()

if __name__ == "__main__":
    main()
