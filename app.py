"""
Wordle Word Checker - SMS & WhatsApp Service
=============================================
Text any 5-letter word → find out if it was a Wordle answer!

Data sources (tried in order):
  1. WordleHints.co.uk API  (real-time, always current)
  2. Local JSON fallback    (works if API is down)

Run update_db.py daily (via cron) to keep the local backup fresh.
"""

import os
import json
import requests
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

# --- Config ---
WORDLEHINTS_API = "https://wordlehints.co.uk/wp-json/wordlehint/v1/answers"
LOCAL_DB_PATH = os.path.join(os.path.dirname(__file__), "wordle_answers.json")


def load_local_db() -> dict:
    """Load local word database. Returns {WORD: {game, date, difficulty}}."""
    if os.path.exists(LOCAL_DB_PATH):
        with open(LOCAL_DB_PATH, "r") as f:
            return json.load(f)
    return {}


def check_via_api(word: str) -> dict | None:
    """Check via WordleHints API (primary source)."""
    try:
        resp = requests.get(
            WORDLEHINTS_API,
            params={"answer": word, "per_page": 1},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if results:
            e = results[0]
            return {
                "found": True, "word": word,
                "game": e.get("game", "?"),
                "date": e.get("date", "unknown"),
                "difficulty": e.get("difficulty", "?"),
            }
        return {"found": False, "word": word}
    except Exception:
        return None  # API unavailable → fall back to local


def check_via_local(word: str) -> dict | None:
    """Check the local JSON database (fallback)."""
    db = load_local_db()
    entry = db.get(word)
    if entry:
        return {
            "found": True, "word": word,
            "game": entry.get("game", "?"),
            "date": entry.get("date", "unknown"),
            "difficulty": entry.get("difficulty", "?"),
        }
    if db:  # DB exists but word not in it
        return {"found": False, "word": word}
    return None  # No local DB


def check_wordle_word(word: str) -> dict:
    """Check if a word has been used as a Wordle answer."""
    word = word.strip().upper()

    if len(word) != 5 or not word.isalpha():
        return {
            "error": True,
            "message": f"'{word}' isn't a valid 5-letter word.\nSend any 5-letter word to check!"
        }

    # Try API first, then local fallback
    result = check_via_api(word)
    if result is not None:
        return result

    result = check_via_local(word)
    if result is not None:
        return result

    return {"error": True, "message": "Service temporarily unavailable. Try again shortly! 🔧"}


def format_response(result: dict) -> str:
    """Format result into a friendly text message."""
    if result.get("error"):
        return f"⚠️ {result['message']}"

    if result["found"]:
        return (
            f"✅ YES! *{result['word']}* was Wordle #{result['game']}\n"
            f"📅 Date: {result['date']}\n"
            f"😅 Difficulty: {result['difficulty']}/10"
        )
    else:
        return (
            f"❌ Nope! *{result['word']}* has NOT been a Wordle answer yet.\n"
            f"It could show up in a future puzzle! 🤞"
        )


# --- Twilio Webhook (handles SMS + WhatsApp) ---

@app.route("/sms", methods=["POST"])
def sms_reply():
    """Respond to incoming SMS/WhatsApp messages."""
    incoming_msg = request.values.get("Body", "").strip()
    resp = MessagingResponse()
    msg = resp.message()

    if incoming_msg.lower() in ("help", "hi", "hello", "hey", "start", "?", "menu"):
        msg.body(
            "🟩🟨⬜ *Wordle Word Checker* ⬜🟨🟩\n\n"
            "Text me any 5-letter word and I'll tell you\n"
            "if it's been used in Wordle!\n\n"
            "Examples: CRANE, ADIEU, STARE\n\n"
            "Just send a word! 🎯"
        )
        return str(resp)

    result = check_wordle_word(incoming_msg)
    msg.body(format_response(result))
    return str(resp)


@app.route("/", methods=["GET"])
def health():
    return "🟩 Wordle Checker is running!", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
