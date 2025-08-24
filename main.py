import os
import re
import sqlite3
import requests
from rapidfuzz import fuzz
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
PROMO_TEXT = os.getenv("PROMO_TEXT", "Join our channel!")
OWNER_IDS = [int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x]
ALLOWED_CHAT_IDS = [int(x) for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",") if x]

# Initialize bot
app = Client("movie_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Initialize database
conn = sqlite3.connect("movie_bot.db", check_same_thread=False)
cursor = conn.cursor()

# Create tables if not exist
cursor.execute("""CREATE TABLE IF NOT EXISTS movies(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    year TEXT,
    file_id TEXT
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS junk_words(word TEXT)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS settings(key TEXT, value TEXT)""")
conn.commit()

# Default confidence thresholds
HIGH_CONFIDENCE = float(os.getenv("HIGH_CONFIDENCE", 82))
LOW_CONFIDENCE = float(os.getenv("LOW_CONFIDENCE", 70))

# Helper: DB settings
def set_setting(key, value):
    cursor.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
    conn.commit()

def get_setting(key, default=None):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default

# Load thresholds from DB if present
HIGH_CONFIDENCE = float(get_setting("high_conf", HIGH_CONFIDENCE))
LOW_CONFIDENCE = float(get_setting("low_conf", LOW_CONFIDENCE))

# Helper: Clean filename
def clean_text(text):
    # Remove extension
    text = re.sub(r"\.(mkv|mp4|avi|mov|wmv)$", "", text, flags=re.IGNORECASE)
    # Remove junk words
    cursor.execute("SELECT word FROM junk_words")
    for row in cursor.fetchall():
        text = text.replace(row[0], "")
    return text.strip()

# Helper: Extract name
def extract_movie_name(filename, caption):
    base_text = ""
    if caption:
        base_text += caption
    if filename:
        base_text += " " + filename
    return clean_text(base_text)

# TMDB Search
def tmdb_search(query):
    url = f"https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": query}
    resp = requests.get(url, params=params)
    data = resp.json()
    if "results" in data and data["results"]:
        best = data["results"][0]
        return best.get("title"), best.get("release_date", "")[:4], best.get("poster_path")
    return None, None, None

# Command: Add junk word
@app.on_message(filters.command("addjunk") & filters.user(OWNER_IDS))
async def add_junk(_, msg):
    if len(msg.command) < 2:
        return await msg.reply("Usage: /addjunk <word>")
    word = msg.command[1]
    cursor.execute("INSERT INTO junk_words VALUES(?)", (word,))
    conn.commit()
    await msg.reply(f"✅ Added junk word: {word}")

# Command: Remove junk word
@app.on_message(filters.command("removejunk") & filters.user(OWNER_IDS))
async def remove_junk(_, msg):
    if len(msg.command) < 2:
        return await msg.reply("Usage: /removejunk <word>")
    word = msg.command[1]
    cursor.execute("DELETE FROM junk_words WHERE word=?", (word,))
    conn.commit()
    await msg.reply(f"❌ Removed junk word: {word}")

# Command: List junk words
@app.on_message(filters.command("listjunk") & filters.user(OWNER_IDS))
async def list_junk(_, msg):
    cursor.execute("SELECT word FROM junk_words")
    words = [r[0] for r in cursor.fetchall()]
    await msg.reply("Current junk words:\n" + ", ".join(words))

# Command: Set confidence thresholds
@app.on_message(filters.command("setconfidence") & filters.user(OWNER_IDS))
async def set_confidence(_, msg):
    if len(msg.command) < 3:
        return await msg.reply("Usage: /setconfidence <high> <low>")
    global HIGH_CONFIDENCE, LOW_CONFIDENCE
    HIGH_CONFIDENCE = float(msg.command[1])
    LOW_CONFIDENCE = float(msg.command[2])
    set_setting("high_conf", HIGH_CONFIDENCE)
    set_setting("low_conf", LOW_CONFIDENCE)
    await msg.reply(f"✅ High: {HIGH_CONFIDENCE}, Low: {LOW_CONFIDENCE}")

# Handle media upload
@app.on_message(filters.chat(ALLOWED_CHAT_IDS) & (filters.video | filters.document))
async def handle_file(_, msg):
    filename = msg.document.file_name if msg.document else msg.video.file_name
    caption = msg.caption or ""
    raw_name = extract_movie_name(filename, caption)

    # Search TMDB and compute fuzzy ratio
    tmdb_name, tmdb_year, _ = tmdb_search(raw_name)
    confidence = fuzz.ratio(raw_name.lower(), (tmdb_name or "").lower())

    if confidence >= HIGH_CONFIDENCE:
        cursor.execute("INSERT INTO movies(name, year, file_id) VALUES(?,?,?)",
                       (tmdb_name, tmdb_year, msg.video.file_id if msg.video else msg.document.file_id))
        conn.commit()
        await msg.reply(f"✅ Saved: {tmdb_name} ({tmdb_year}) [{confidence}%]")
    elif confidence >= LOW_CONFIDENCE:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm", callback_data=f"confirm|{tmdb_name}|{tmdb_year}|{msg.id}"),
             InlineKeyboardButton("✏️ Rename", callback_data=f"rename|{msg.id}"),
             InlineKeyboardButton("❌ Ignore", callback_data=f"ignore|{msg.id}")]
        ])
        await msg.reply(f"🤔 Not sure. Detected: {tmdb_name} ({confidence}%)", reply_markup=kb)
    else:
        await msg.reply("⚠️ Could not confidently detect movie. Use rename manually.")

# Command: Get movie
@app.on_message(filters.command("get"))
async def get_movie(_, msg):
    if len(msg.command) < 2:
        return await msg.reply("Usage: /get <movie name>")
    name = msg.text.split(" ", 1)[1]
    cursor.execute("SELECT file_id, name, year FROM movies WHERE name LIKE ?", (f"%{name}%",))
    row = cursor.fetchone()
    if not row:
        return await msg.reply("❌ Not found.")
    await msg.reply_video(row[0], caption=f"{row[1]} ({row[2]})\n\n{PROMO_TEXT}")

# Start bot
app.run()
