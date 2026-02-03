"""
Wordle Word Checker - SMS & WhatsApp Service
=============================================
Text any 5-letter word → find out if it was a Wordle answer!
If it was used, suggests a similar word that hasn't been played yet.

Data sources:
  1. WordleHints.co.uk API  (real-time, always current)
  2. Local JSON fallback    (works if API is down)
"""

import os
import json
import time
import random
import requests
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

# --- Config ---
WORDLEHINTS_API = "https://wordlehints.co.uk/wp-json/wordlehint/v1/answers"
LOCAL_DB_PATH = os.path.join(os.path.dirname(__file__), "wordle_answers.json")
WORDLIST_PATH = os.path.join(os.path.dirname(__file__), "wordlist.txt")

# --- In-memory cache for past answers ---
_past_answers_cache = {"words": set(), "last_fetched": 0}
CACHE_TTL = 3600  # Refresh every hour


def load_wordlist() -> set:
    """Load the common 5-letter words list for suggestions."""
    if os.path.exists(WORDLIST_PATH):
        with open(WORDLIST_PATH, "r") as f:
            return {w.strip().upper() for w in f if len(w.strip()) == 5}
    return set()


def load_local_db() -> dict:
    """Load local word database. Returns {WORD: {game, date, difficulty}}."""
    if os.path.exists(LOCAL_DB_PATH):
        with open(LOCAL_DB_PATH, "r") as f:
            return json.load(f)
    return {}


def get_past_answers() -> set:
    """Get all past Wordle answers (cached, refreshed hourly)."""
    now = time.time()
    if _past_answers_cache["words"] and (now - _past_answers_cache["last_fetched"]) < CACHE_TTL:
        return _past_answers_cache["words"]

    # Try to load from local DB first
    db = load_local_db()
    if db:
        _past_answers_cache["words"] = set(db.keys())
        _past_answers_cache["last_fetched"] = now
        return _past_answers_cache["words"]

    # Try API — fetch all answers
    all_words = set()
    page = 1
    try:
        while True:
            resp = requests.get(
                WORDLEHINTS_API,
                params={"page": page, "per_page": 100},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                break
            for entry in results:
                word = entry.get("answer", "").upper()
                if word:
                    all_words.add(word)
            if page * 100 >= data.get("total", 0):
                break
            page += 1
            time.sleep(0.3)
    except Exception:
        pass

    if all_words:
        _past_answers_cache["words"] = all_words
        _past_answers_cache["last_fetched"] = now
    return _past_answers_cache["words"]


def similarity_score(word1: str, word2: str) -> float:
    """
    Score similarity between two words.
    - Exact position match = 2 points
    - Letter present but wrong position = 1 point
    """
    score = 0.0
    for i in range(5):
        if word1[i] == word2[i]:
            score += 2.0
        elif word1[i] in word2:
            score += 1.0
    return score


def find_similar_unused(word: str, max_suggestions: int = 3) -> list:
    """
    Find similar words that haven't been used in Wordle.
    Returns a list of suggestion strings.
    """
    past = get_past_answers()
    wordlist = load_wordlist()

    if not wordlist:
        return []

    # Words that haven't been used yet
    unused = wordlist - past

    if not unused:
        return []

    # Score all unused words by similarity to the input
    scored = []
    for candidate in unused:
        if candidate == word:
            continue
        s = similarity_score(word, candidate)
        if s >= 3.0:  # At least somewhat similar
            scored.append((candidate, s))

    # Sort by score descending, then pick top suggestions
    scored.sort(key=lambda x: -x[1])

    # Pick top matches, with a touch of variety
    top = scored[:max_suggestions * 2]
    if len(top) > max_suggestions:
        top = top[:max_suggestions]

    return [w for w, s in top]


# --- Word Checking ---

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
        return None


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
    if db:
        return {"found": False, "word": word}
    return None


def check_wordle_word(word: str) -> dict:
    """Check if a word has been used as a Wordle answer."""
    word = word.strip().upper()

    if len(word) != 5 or not word.isalpha():
        return {
            "error": True,
            "message": f"'{word}' isn't a valid 5-letter word.\nSend any 5-letter word to check!"
        }

    result = check_via_api(word)
    if result is not None:
        return result

    result = check_via_local(word)
    if result is not None:
        return result

    return {"error": True, "message": "Service temporarily unavailable. Try again shortly! 🔧"}


def format_response(result: dict) -> str:
    """Format result into a friendly text message with suggestions."""
    if result.get("error"):
        return f"⚠️ {result['message']}"

    if result["found"]:
        msg = (
            f"✅ YES! *{result['word']}* was Wordle #{result['game']}\n"
            f"📅 Date: {result['date']}\n"
            f"😅 Difficulty: {result['difficulty']}/10"
        )
        # Find and suggest similar unused words
        suggestions = find_similar_unused(result["word"])
        if suggestions:
            formatted = ", ".join(f"*{s}*" for s in suggestions)
            msg += f"\n\n💡 Try instead: {formatted}"
        return msg
    else:
        return (
            f"❌ Nope! *{result['word']}* has NOT been a Wordle answer yet.\n"
            f"It could show up in a future puzzle! 🤞"
        )


# --- Twilio Webhook ---

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
            "If it was used, I'll suggest similar words\n"
            "that haven't been played yet. 💡\n\n"
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
