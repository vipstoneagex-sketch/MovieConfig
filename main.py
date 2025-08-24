import os
import sqlite3
import requests
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from rapidfuzz import fuzz

# ✅ Load Environment Variables
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# ✅ Confidence Thresholds (can be stored in DB later)
HIGH_CONFIDENCE = 82
LOW_CONFIDENCE = 70

# ✅ Database Setup
conn = sqlite3.connect("movies.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""CREATE TABLE IF NOT EXISTS movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    year TEXT,
    file_id TEXT
)""")
cursor.execute("""CREATE TABLE IF NOT EXISTS junk_words (word TEXT)""")
conn.commit()

# ✅ Initialize Bot
app = Client("MovieBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ✅ Helper: Clean Name
def clean_name(text):
    cursor.execute("SELECT word FROM junk_words")
    junk_words = [row[0].lower() for row in cursor.fetchall()]
    words = text.replace("_", " ").split()
    filtered = [w for w in words if w.lower() not in junk_words]
    return " ".join(filtered)

# ✅ TMDB Search
def search_tmdb(query):
    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}"
    r = requests.get(url).json()
    if r.get("results"):
        movie = r["results"][0]
        return {
            "title": movie["title"],
            "year": movie.get("release_date", "").split("-")[0],
            "poster": f"https://image.tmdb.org/t/p/w500{movie['poster_path']}" if movie.get("poster_path") else None
        }
    return None

# ✅ Admin Panel Buttons
def admin_buttons(movie_name, file_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm & Save", callback_data=f"confirm:{movie_name}:{file_id}")],
        [InlineKeyboardButton("✏️ Rename", callback_data=f"rename:{file_id}")],
        [InlineKeyboardButton("❌ Ignore", callback_data="ignore")]
    ])

# ✅ Save Movie
def save_movie(title, year, file_id):
    cursor.execute("INSERT INTO movies (title, year, file_id) VALUES (?, ?, ?)", (title, year, file_id))
    conn.commit()

# ✅ On File Upload
@app.on_message(filters.document | filters.video & filters.private)
async def handle_upload(client, message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Only admin can upload movies!")
        return

    file_name = message.document.file_name if message.document else message.video.file_name
    cleaned = clean_name(file_name)
    tmdb_data = search_tmdb(cleaned)

    if tmdb_data:
        ratio = fuzz.token_sort_ratio(cleaned.lower(), tmdb_data["title"].lower())
        if ratio >= HIGH_CONFIDENCE:
            save_movie(tmdb_data["title"], tmdb_data["year"], message.document.file_id)
            await message.reply(f"✅ Saved: {tmdb_data['title']} ({tmdb_data['year']})")
        elif LOW_CONFIDENCE <= ratio < HIGH_CONFIDENCE:
            await message.reply_photo(
                tmdb_data["poster"],
                caption=f"Low confidence match ({ratio}%).\nDetected: {tmdb_data['title']} ({tmdb_data['year']})",
                reply_markup=admin_buttons(tmdb_data["title"], message.document.file_id)
            )
        else:
            await message.reply("❌ Could not identify this movie.")
    else:
        await message.reply("❌ No results from TMDB.")

# ✅ Inline Button Actions
@app.on_callback_query()
async def callbacks(client, query):
    data = query.data.split(":")
    if data[0] == "confirm":
        title, file_id = data[1], data[2]
        save_movie(title, "Unknown", file_id)
        await query.message.edit_text(f"✅ Movie saved as {title}")
    elif data[0] == "rename":
        await query.message.reply("Send me the correct name now:")
    elif data[0] == "ignore":
        await query.message.edit_text("❌ Ignored.")

# ✅ User Commands
@app.on_message(filters.command("get") & filters.private)
async def get_movie(client, message):
    query = message.text.split(" ", 1)[1]
    cursor.execute("SELECT file_id FROM movies WHERE title LIKE ?", (f"%{query}%",))
    row = cursor.fetchone()
    if row:
        await message.reply_document(row[0])
    else:
        await message.reply("Movie not found!")

@app.on_message(filters.command("addjunk") & filters.private)
async def add_junk(client, message):
    if message.from_user.id != ADMIN_ID: return
    word = message.text.split(" ", 1)[1]
    cursor.execute("INSERT INTO junk_words VALUES (?)", (word,))
    conn.commit()
    await message.reply(f"✅ Added junk word: {word}")

# ✅ Start Bot
print("✅ Movie Bot is running...")
app.run()