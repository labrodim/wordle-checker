"""
Wordle Word Checker - SMS & WhatsApp Service
=============================================
HARDCORE MODE: Pure regex + probability-based suggestions

INPUT FORMAT:
  - UPPERCASE = 🟩 GREEN (locked in that position)
  - space before letter = 🟨 YELLOW (in word, wrong spot)
  - lowercase = ⬜ GREY (not in word)

EXAMPLES:
  st aRe → s,t,e grey | a yellow | R green
  cr aNE → c,r grey | a yellow | N,E green
  
MORE RESULTS:
  stARe+  → 6 suggestions
  stARe++ → 9 suggestions
  +       → 3 more from last search
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
_user_sessions = {}  # {phone: {"last_query": ..., "last_offset": ..., "last_results": ...}}
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
# PATTERN PARSER
# ============================================

def parse_input(raw_input: str) -> dict:
    """
    Parse input with:
    - UPPERCASE = green (locked in position)
    - space before letter = yellow (in word, not this position)
    - lowercase = grey (not in word)
    
    Also handles + suffix for more results.
    
    Returns: {
        "word": the 5-letter word (uppercase),
        "green": {pos: letter},     # locked positions
        "yellow": {pos: letter},    # must be in word, not here
        "grey": set of letters,     # exclude these
        "extra_count": 0/3/6        # from + or ++
    }
    """
    # Check for + suffix
    extra_count = 0
    clean_input = raw_input.rstrip('+')
    plus_count = len(raw_input) - len(clean_input)
    extra_count = plus_count * 3
    
    # Parse the pattern
    green = {}
    yellow = {}
    grey = set()
    word_chars = []
    
    pos = 0
    i = 0
    prev_space = False
    
    while i < len(clean_input):
        ch = clean_input[i]
        
        if ch == ' ':
            prev_space = True
            i += 1
            continue
        
        if ch.isalpha():
            letter = ch.upper()
            word_chars.append(letter)
            
            if ch.isupper():
                # GREEN - locked
                green[pos] = letter
            elif prev_space:
                # YELLOW - in word, not here
                yellow[pos] = letter
            else:
                # GREY - not in word
                grey.add(letter)
            
            pos += 1
            prev_space = False
        
        i += 1
    
    word = "".join(word_chars)
    
    # Remove yellow letters from grey (they're in the word!)
    yellow_letters = set(yellow.values())
    grey = grey - yellow_letters
    
    # Remove green letters from grey
    green_letters = set(green.values())
    grey = grey - green_letters
    
    return {
        "word": word,
        "green": green,
        "yellow": yellow,
        "grey": grey,
        "extra_count": extra_count
    }


# ============================================
# HARDCORE REGEX + PROBABILITY ENGINE
# ============================================

def filter_candidates(parsed: dict) -> list:
    """
    Filter words using regex and letter rules.
    """
    wordlist = load_wordlist()
    past = get_past_answers()
    available = wordlist - past
    
    green = parsed["green"]
    yellow = parsed["yellow"]
    grey = parsed["grey"]
    
    # Build regex pattern for green letters
    pattern_chars = []
    for i in range(5):
        if i in green:
            pattern_chars.append(green[i])
        else:
            pattern_chars.append(".")
    
    regex_pattern = "".join(pattern_chars)
    pattern = re.compile(f"^{regex_pattern}$")
    
    # Filter candidates
    matches = []
    yellow_letters = set(yellow.values())
    yellow_positions = yellow  # {pos: letter} - letter must NOT be in this pos
    
    for word in available:
        # Must match green pattern
        if not pattern.match(word):
            continue
        
        # Must not contain grey letters
        if any(letter in word for letter in grey):
            continue
        
        # Must contain all yellow letters
        if not all(letter in word for letter in yellow_letters):
            continue
        
        # Yellow letters must NOT be in their original positions
        valid = True
        for pos, letter in yellow_positions.items():
            if pos < len(word) and word[pos] == letter:
                valid = False
                break
        
        if valid:
            matches.append(word)
    
    return sorted(matches)


def analyze_position_frequencies(candidates: list, locked_positions: dict) -> dict:
    """Count letter frequencies at each unlocked position."""
    position_freq = {i: Counter() for i in range(5) if i not in locked_positions}
    
    for word in candidates:
        for pos in position_freq:
            position_freq[pos][word[pos]] += 1
    
    return position_freq


def score_suggestion(word: str, position_freq: dict, tested_letters: set, locked_positions: dict) -> float:
    """Score based on testing high-frequency letters."""
    score = 0.0
    seen = set()
    
    for i, ch in enumerate(word):
        if i in locked_positions:
            continue
        
        if ch in seen:
            score -= 5.0
        seen.add(ch)
        
        if ch in tested_letters:
            continue
        
        if i in position_freq and ch in position_freq[i]:
            total = sum(position_freq[i].values())
            if total > 0:
                freq_pct = position_freq[i][ch] / total
                score += freq_pct * 10.0
    
    return score


def find_best_suggestions(parsed: dict, count: int = 3, offset: int = 0) -> dict:
    """
    Find optimal next guesses.
    
    Returns dict with pattern info and ranked suggestions.
    """
    candidates = filter_candidates(parsed)
    
    green = parsed["green"]
    yellow = parsed["yellow"]
    grey = parsed["grey"]
    
    # Build display pattern
    display = []
    for i in range(5):
        if i in green:
            display.append(f"[{green[i]}]")  # Green
        elif i in yellow:
            display.append(f"({yellow[i]})")  # Yellow
        else:
            display.append("_")
    pattern_display = "".join(display)
    
    if not candidates:
        return {
            "pattern_display": pattern_display,
            "candidates_count": 0,
            "suggestions": [],
            "has_more": False
        }
    
    # If few candidates, just return them
    if len(candidates) <= count + offset:
        return {
            "pattern_display": pattern_display,
            "candidates_count": len(candidates),
            "suggestions": candidates[offset:offset+count],
            "has_more": False
        }
    
    # Score candidates by probability value
    position_freq = analyze_position_frequencies(candidates, green)
    tested = grey | set(yellow.values()) | set(green.values())
    
    scored = []
    for candidate in candidates:
        s = score_suggestion(candidate, position_freq, tested, green)
        scored.append((candidate, s))
    
    scored.sort(key=lambda x: -x[1])
    
    suggestions = [w for w, s in scored[offset:offset+count]]
    has_more = len(scored) > offset + count
    
    return {
        "pattern_display": pattern_display,
        "candidates_count": len(candidates),
        "suggestions": suggestions,
        "has_more": has_more,
        "all_scored": scored  # For session storage
    }


# ============================================
# BEST STARTER WORD
# ============================================

def get_recent_answers(n: int = 20) -> list:
    """Get the N most recent Wordle answers."""
    db = load_local_db()
    if not db:
        return []
    
    # Sort by date descending
    sorted_words = sorted(db.items(), key=lambda x: x[1].get("date", ""), reverse=True)
    return [word for word, _ in sorted_words[:n]]


def get_best_starter() -> dict:
    """
    Find the best starting word:
    - 5 unique letters
    - High frequency letters in remaining candidates
    - Dissimilar to recent answers (avoid their letters)
    """
    wordlist = load_wordlist()
    past = get_past_answers()
    available = wordlist - past
    
    if not available:
        return {"word": "SALET", "letters": "S, A, L, E, T"}
    
    # Get recent answers to avoid their patterns
    recent = get_recent_answers(20)
    recent_letters = Counter()
    for word in recent:
        for ch in word:
            recent_letters[ch] += 1
    
    # Count letter frequency in available words
    letter_freq = Counter()
    for word in available:
        for ch in set(word):  # Count each letter once per word
            letter_freq[ch] += 1
    
    # Score each available word
    best_word = None
    best_score = -1
    
    for word in available:
        # Must have 5 unique letters
        if len(set(word)) != 5:
            continue
        
        score = 0
        for ch in word:
            # Add frequency score
            score += letter_freq.get(ch, 0)
            # Penalize letters from recent answers
            score -= recent_letters.get(ch, 0) * 2
        
        if score > best_score:
            best_score = score
            best_word = word
    
    if not best_word:
        best_word = "SALET"
    
    letters = ", ".join(best_word)
    return {"word": best_word, "letters": letters}


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
            "message": f"'{word_upper}' isn't a valid 5-letter word."
        }

    result = check_via_api(word_upper)
    if result is not None:
        return result

    result = check_via_local(word_upper)
    if result is not None:
        return result

    return {"error": True, "message": "Service temporarily unavailable. Try again shortly! 🔧"}


def format_response(result: dict, parsed: dict, analysis: dict) -> str:
    """Format response with suggestions."""
    if result.get("error"):
        return f"⚠️ {result['message']}"

    msg_parts = []
    
    if result["found"]:
        msg_parts.append(
            f"✅ *{result['word']}* was Wordle #{result['game']}\n"
            f"📅 {result['date']} | Difficulty: {result['difficulty']}/10"
        )
    else:
        msg_parts.append(
            f"❌ *{result['word']}* hasn't been used yet.\n"
            f"Could be a future puzzle! 🤞"
        )
    
    # Add analysis
    if analysis["candidates_count"] > 0:
        msg_parts.append(f"\n🎯 Pattern: {analysis['pattern_display']}")
        msg_parts.append(f"📊 {analysis['candidates_count']} possible words")
        
        if analysis["suggestions"]:
            formatted = ", ".join(f"*{s}*" for s in analysis["suggestions"])
            msg_parts.append(f"💡 Best: {formatted}")
        
        if analysis["has_more"]:
            msg_parts.append("📌 Send *+* for more")
    
    return "\n".join(msg_parts)


# ============================================
# TWILIO WEBHOOK
# ============================================

@app.route("/sms", methods=["POST"])
def sms_reply():
    """Handle incoming SMS/WhatsApp."""
    incoming_msg = request.values.get("Body", "").strip()
    from_number = request.values.get("From", "unknown")
    
    resp = MessagingResponse()
    msg = resp.message()

    # Help
    if incoming_msg.lower() in ("help", "hi", "hello", "hey", "start", "menu"):
        msg.body(
            "🟩🟨⬜ *Wordle Solver* ⬜🟨🟩\n\n"
            "Send your guess with colors:\n"
            "• *UPPERCASE* = 🟩 green\n"
            "• *space before* = 🟨 yellow\n"
            "• *lowercase* = ⬜ grey\n\n"
            "Example: *st aRe*\n"
            "→ s,t,e grey | a yellow | R green\n\n"
            "*?* = best starting word\n"
            "*+* = more results\n"
            "*stARe++* = lots more\n\n"
            "Pure math. No guessing. 📊"
        )
        return str(resp)
    
    # Handle "?" for best starting word
    if incoming_msg.strip() == "?":
        suggestion = get_best_starter()
        msg.body(
            f"🎯 *Best opener: {suggestion['word']}*\n\n"
            f"📊 Tests top letters: {suggestion['letters']}\n"
            f"🔀 Avoids recent patterns\n\n"
            f"Send it, then tell me what you got!"
        )
        return str(resp)
    
    # Handle "+" for more results
    if incoming_msg.strip('+') == '':
        plus_count = len(incoming_msg)
        extra = plus_count * 3
        
        session = _user_sessions.get(from_number)
        if session and "all_scored" in session:
            offset = session.get("offset", 3)
            scored = session["all_scored"]
            
            suggestions = [w for w, s in scored[offset:offset+extra]]
            has_more = len(scored) > offset + extra
            
            if suggestions:
                _user_sessions[from_number]["offset"] = offset + extra
                formatted = ", ".join(f"*{s}*" for s in suggestions)
                reply = f"💡 More: {formatted}"
                if has_more:
                    reply += "\n📌 Send *+* for more"
                msg.body(reply)
            else:
                msg.body("No more suggestions for that pattern.")
        else:
            msg.body("No previous search. Send a word first!")
        return str(resp)
    
    # Parse input
    parsed = parse_input(incoming_msg)
    
    if len(parsed["word"]) != 5:
        msg.body("⚠️ Need exactly 5 letters.\nExample: st aRe")
        return str(resp)
    
    # Check if word was used
    result = check_wordle_word(parsed["word"])
    
    # Get suggestions
    base_count = 3 + parsed["extra_count"]
    analysis = find_best_suggestions(parsed, count=base_count)
    
    # Store session for "+" follow-up
    _user_sessions[from_number] = {
        "parsed": parsed,
        "offset": base_count,
        "all_scored": analysis.get("all_scored", [])
    }
    
    # Format and send
    msg.body(format_response(result, parsed, analysis))
    return str(resp)


@app.route("/", methods=["GET"])
def health():
    return "🟩 Wordle Solver (Hardcore Mode) is running!", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
