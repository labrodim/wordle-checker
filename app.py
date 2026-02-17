"""
Wordle Word Checker - SMS & WhatsApp Service
=============================================
HARDCORE MODE: Pure regex matching + probability-based suggestions

Uppercase = GREEN (locked in that position)
Suggestions maximize your odds by testing the most common letters
among remaining candidates.

No heuristics, no guessing — just regex and frequency analysis.
"""

import os
import re
import json
import time
import requests
from collections import Counter
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

# --- Config ---
WORDLEHINTS_API = "https://wordlehints.co.uk/wp-json/wordlehint/v1/answers"
LOCAL_DB_PATH = os.path.join(os.path.dirname(__file__), "wordle_answers.json")
WORDLIST_PATH = os.path.join(os.path.dirname(__file__), "wordlist.txt")

# --- In-memory cache ---
_past_answers_cache = {"words": set(), "last_fetched": 0}
_wordlist_cache = {"words": set(), "loaded": False}
CACHE_TTL = 3600


def load_wordlist() -> set:
    """Load word list (cached)."""
    if _wordlist_cache["loaded"]:
        return _wordlist_cache["words"]
    
    if os.path.exists(WORDLIST_PATH):
        with open(WORDLIST_PATH, "r") as f:
            _wordlist_cache["words"] = {w.strip().upper() for w in f if len(w.strip()) == 5}
            _wordlist_cache["loaded"] = True
    return _wordlist_cache["words"]


def load_local_db() -> dict:
    """Load local word database."""
    if os.path.exists(LOCAL_DB_PATH):
        with open(LOCAL_DB_PATH, "r") as f:
            return json.load(f)
    return {}


def get_past_answers() -> set:
    """Get all past Wordle answers (cached)."""
    now = time.time()
    if _past_answers_cache["words"] and (now - _past_answers_cache["last_fetched"]) < CACHE_TTL:
        return _past_answers_cache["words"]

    db = load_local_db()
    if db:
        _past_answers_cache["words"] = set(db.keys())
        _past_answers_cache["last_fetched"] = now
        return _past_answers_cache["words"]

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


# ============================================
# HARDCORE REGEX + PROBABILITY ENGINE
# ============================================

def parse_pattern(raw_word: str) -> tuple:
    """
    Parse input into regex pattern and metadata.
    
    Uppercase = GREEN (locked in position)
    Lowercase = tested but not locked (grey or yellow)
    
    Returns: (word_upper, regex_pattern, locked_positions, tested_letters)
    
    Example: "crANe" 
    → ("CRANE", "..AN.", {2: 'A', 3: 'N'}, {'C', 'R', 'E'})
    """
    word_upper = raw_word.upper()
    locked = {}
    tested = set()
    pattern_chars = []
    
    for i, ch in enumerate(raw_word):
        if ch.isupper():
            # GREEN - locked in this position
            locked[i] = ch.upper()
            pattern_chars.append(ch.upper())
        else:
            # Tested but not green
            tested.add(ch.upper())
            pattern_chars.append(".")
    
    regex_pattern = "".join(pattern_chars)
    return word_upper, regex_pattern, locked, tested


def filter_candidates(regex_pattern: str, exclude_used: bool = True) -> list:
    """
    Get all words matching the regex pattern.
    Excludes past Wordle answers by default.
    """
    wordlist = load_wordlist()
    past = get_past_answers() if exclude_used else set()
    
    available = wordlist - past
    
    pattern = re.compile(f"^{regex_pattern}$")
    matches = [w for w in available if pattern.match(w)]
    
    return sorted(matches)


def analyze_position_frequencies(candidates: list, locked_positions: dict) -> dict:
    """
    For each unlocked position, count letter frequencies among candidates.
    
    Returns: {position: Counter({letter: count, ...}), ...}
    """
    position_freq = {i: Counter() for i in range(5) if i not in locked_positions}
    
    for word in candidates:
        for pos in position_freq:
            position_freq[pos][word[pos]] += 1
    
    return position_freq


def score_suggestion(word: str, position_freq: dict, tested_letters: set, locked_positions: dict) -> float:
    """
    Score a word based on how well it tests high-probability letters.
    
    Higher score = tests more common letters in positions that need testing.
    """
    score = 0.0
    seen = set()
    
    for i, ch in enumerate(word):
        # Skip locked positions - we already know those
        if i in locked_positions:
            continue
        
        # Penalize repeat letters within the word
        if ch in seen:
            score -= 5.0
        seen.add(ch)
        
        # Skip letters we've already tested
        if ch in tested_letters:
            continue
        
        # Score based on frequency of this letter in this position
        if i in position_freq and ch in position_freq[i]:
            # Normalize: what % of candidates have this letter here?
            total = sum(position_freq[i].values())
            if total > 0:
                freq_pct = position_freq[i][ch] / total
                score += freq_pct * 10.0  # Weight it
    
    return score


def find_best_suggestions(raw_word: str, max_suggestions: int = 3) -> dict:
    """
    MAIN ENGINE: Find optimal next guesses.
    
    Returns dict with:
    - pattern: the regex pattern used
    - candidates_count: how many words match
    - suggestions: list of best words to try
    - locked_display: visual of locked positions
    """
    word_upper, regex_pattern, locked, tested = parse_pattern(raw_word)
    
    # Get all matching candidates
    candidates = filter_candidates(regex_pattern)
    
    if not candidates:
        return {
            "pattern": regex_pattern,
            "candidates_count": 0,
            "suggestions": [],
            "locked_display": None,
            "sample_candidates": []
        }
    
    # Build locked position display
    locked_display = ["_"] * 5
    for pos, letter in locked.items():
        locked_display[pos] = letter
    locked_str = " ".join(locked_display)
    
    # If only a few candidates, just return them
    if len(candidates) <= max_suggestions:
        return {
            "pattern": regex_pattern,
            "candidates_count": len(candidates),
            "suggestions": candidates,
            "locked_display": locked_str,
            "sample_candidates": candidates[:5]
        }
    
    # Analyze letter frequencies in each position
    position_freq = analyze_position_frequencies(candidates, locked)
    
    # Score all candidates
    scored = []
    for candidate in candidates:
        if candidate == word_upper:
            continue
        s = score_suggestion(candidate, position_freq, tested, locked)
        scored.append((candidate, s))
    
    # Sort by score descending
    scored.sort(key=lambda x: -x[1])
    
    suggestions = [w for w, s in scored[:max_suggestions]]
    
    return {
        "pattern": regex_pattern,
        "candidates_count": len(candidates),
        "suggestions": suggestions,
        "locked_display": locked_str,
        "sample_candidates": candidates[:5]
    }


# ============================================
# WORDLE CHECK API
# ============================================

def check_via_api(word: str) -> dict:
    """Check via WordleHints API."""
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


def check_via_local(word: str) -> dict:
    """Check local database."""
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
    """Check if a word has been used."""
    word_upper = word.strip().upper()

    if len(word_upper) != 5 or not word_upper.isalpha():
        return {
            "error": True,
            "message": f"'{word_upper}' isn't a valid 5-letter word.\nSend any 5-letter word to check!"
        }

    result = check_via_api(word_upper)
    if result is not None:
        return result

    result = check_via_local(word_upper)
    if result is not None:
        return result

    return {"error": True, "message": "Service temporarily unavailable. Try again shortly! 🔧"}


def format_response(result: dict, raw_word: str) -> str:
    """Format response with probability-based suggestions."""
    if result.get("error"):
        return f"⚠️ {result['message']}"

    if result["found"]:
        msg = (
            f"✅ YES! *{result['word']}* was Wordle #{result['game']}\n"
            f"📅 Date: {result['date']}\n"
            f"😅 Difficulty: {result['difficulty']}/10"
        )
        
        # Get probability-based suggestions
        analysis = find_best_suggestions(raw_word)
        
        if analysis["suggestions"]:
            formatted = ", ".join(f"*{s}*" for s in analysis["suggestions"])
            
            if analysis["locked_display"] and "_" in analysis["locked_display"]:
                msg += f"\n\n🎯 Pattern: [{analysis['locked_display']}]"
                msg += f"\n📊 {analysis['candidates_count']} possible words"
                msg += f"\n💡 Best bets: {formatted}"
            else:
                msg += f"\n\n💡 Try instead: {formatted}"
        
        return msg
    else:
        # Word not used - also give suggestions for this pattern
        analysis = find_best_suggestions(raw_word)
        
        msg = (
            f"❌ Nope! *{result['word']}* has NOT been a Wordle answer yet.\n"
            f"It could be a future puzzle! 🤞"
        )
        
        if analysis["locked_display"] and "_" in analysis["locked_display"]:
            if analysis["candidates_count"] > 0:
                msg += f"\n\n🎯 Pattern: [{analysis['locked_display']}]"
                msg += f"\n📊 {analysis['candidates_count']} words match"
                if analysis["suggestions"]:
                    formatted = ", ".join(f"*{s}*" for s in analysis["suggestions"])
                    msg += f"\n💡 High-prob: {formatted}"
        
        return msg


# ============================================
# TWILIO WEBHOOK
# ============================================

@app.route("/sms", methods=["POST"])
def sms_reply():
    """Handle incoming SMS/WhatsApp."""
    incoming_msg = request.values.get("Body", "").strip()
    resp = MessagingResponse()
    msg = resp.message()

    if incoming_msg.lower() in ("help", "hi", "hello", "hey", "start", "?", "menu"):
        msg.body(
            "🟩🟨⬜ *Wordle Word Checker* ⬜🟨🟩\n\n"
            "Text any 5-letter word to check if it's been used.\n\n"
            "🎯 *UPPERCASE = GREEN letters!*\n"
            "I'll find words matching that pattern\n"
            "and suggest highest-probability picks.\n\n"
            "Example: *stARe* → locks A in pos 3, R in pos 4\n"
            "Shows how many words match + best guesses.\n\n"
            "Pure regex + probability. No guessing. 📊"
        )
        return str(resp)

    result = check_wordle_word(incoming_msg)
    msg.body(format_response(result, incoming_msg))
    return str(resp)


@app.route("/", methods=["GET"])
def health():
    return "🟩 Wordle Checker is running! (Hardcore Mode)", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
