"""
Wordle Solver - SMS & WhatsApp Service
======================================
HARDCORE MODE: Pure regex + probability-based suggestions

INPUT FORMAT:
  - UPPERCASE = 🟩 GREEN (locked in position)
  - (letter)  = 🟨 YELLOW (in word, wrong spot)
  - lowercase = ⬜ GREY (not in word)

EXAMPLES:
  st(a)Re → s,t,e grey | a yellow | R green
  cr(a)NE → c,r grey | a yellow | N,E green
  
COMMANDS:
  ?   = best starting word
  +   = more suggestions
  ++  = even more
  *   = reset/new game
"""

import os
import re
import json
import time
import requests
from io import BytesIO
from collections import Counter
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

app = Flask(__name__)

# --- Config ---
WORDLEHINTS_API = "https://wordlehints.co.uk/wp-json/wordlehint/v1/answers"
LOCAL_DB_PATH = os.path.join(os.path.dirname(__file__), "wordle_answers.json")
WORDLIST_PATH = os.path.join(os.path.dirname(__file__), "wordlist.txt")

# --- In-memory cache ---
_past_answers_cache = {"words": set(), "last_fetched": 0}
_wordlist_cache = {"words": set(), "loaded": False}
_user_sessions = {}  # {phone: {"suggestions": [...], "index": 0}}
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
# IMAGE PROCESSING FOR SCREENSHOTS
# ============================================

# Wordle color ranges (RGB) - handles light and dark mode
WORDLE_COLORS = {
    "green": [
        (83, 141, 78),    # Light mode green
        (106, 170, 100),  # Classic green
        (97, 140, 85),    # Dark mode green
        (83, 141, 78),    # NYT green
    ],
    "yellow": [
        (181, 159, 59),   # Light mode yellow
        (201, 180, 88),   # Classic yellow
        (177, 160, 76),   # Dark mode yellow
        (196, 180, 84),   # NYT yellow
    ],
    "grey": [
        (120, 124, 126),  # Light mode grey
        (121, 124, 126),  # Classic grey
        (58, 58, 60),     # Dark mode grey
        (134, 136, 138),  # Another grey variant
        (162, 162, 162),  # Lighter grey
    ]
}

def color_distance(c1, c2):
    """Euclidean distance between two RGB colors."""
    return ((c1[0]-c2[0])**2 + (c1[1]-c2[1])**2 + (c1[2]-c2[2])**2) ** 0.5

def classify_color(rgb):
    """Classify an RGB color as green, yellow, or grey."""
    min_dist = float('inf')
    best_match = "grey"
    
    for color_name, color_list in WORDLE_COLORS.items():
        for ref_color in color_list:
            dist = color_distance(rgb, ref_color)
            if dist < min_dist:
                min_dist = dist
                best_match = color_name
    
    # Threshold - if too far from all known colors, default to grey
    if min_dist > 80:
        # Check if it's more green-ish or yellow-ish by hue
        r, g, b = rgb
        if g > r and g > b and g > 100:
            return "green"
        elif r > 150 and g > 150 and b < 150:
            return "yellow"
        return "grey"
    
    return best_match

def fetch_image_from_url(url):
    """Fetch image from Twilio media URL."""
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content)).convert('RGB')
    except Exception as e:
        print(f"Error fetching image: {e}")
        return None

def find_wordle_grid(image):
    """
    Find the Wordle grid in the image.
    Returns list of rows, each row is list of (x, y, width, height) for cells.
    """
    width, height = image.size
    pixels = image.load()
    
    # Strategy: scan for rows of 5 similarly-colored squares
    # Wordle cells are typically squares with consistent spacing
    
    # Sample horizontal lines looking for cell patterns
    cell_size_estimate = width // 8  # Rough estimate
    min_cell = cell_size_estimate // 2
    max_cell = cell_size_estimate * 2
    
    rows_found = []
    
    # Scan vertical positions
    y = height // 6  # Start from upper portion
    while y < height * 0.7:
        # Look for a row of cells at this y
        row = find_row_at_y(image, y, min_cell, max_cell)
        if row and len(row) == 5:
            rows_found.append(row)
            y += row[0][3] + 5  # Skip past this row
        else:
            y += 10
    
    return rows_found

def find_row_at_y(image, y, min_cell, max_cell):
    """Find a row of 5 cells at approximately y position."""
    width, height = image.size
    pixels = image.load()
    
    # Look for consistent colored blocks
    cells = []
    x = 0
    
    while x < width - min_cell:
        # Sample color at this position
        sample_y = min(y, height - 1)
        sample_x = min(x, width - 1)
        
        color = pixels[sample_x, sample_y]
        color_type = classify_color(color)
        
        # If not a background color, might be a cell
        if color_type in ("green", "yellow", "grey"):
            # Try to find cell boundaries
            cell = find_cell_bounds(image, x, y, min_cell, max_cell)
            if cell:
                cells.append(cell)
                x = cell[0] + cell[2] + 2  # Move past this cell
                continue
        
        x += 5
    
    return cells if len(cells) == 5 else None

def find_cell_bounds(image, start_x, start_y, min_size, max_size):
    """Find boundaries of a cell starting near (start_x, start_y)."""
    width, height = image.size
    pixels = image.load()
    
    # Get the color at start position
    sy = min(start_y, height - 1)
    sx = min(start_x, width - 1)
    start_color = pixels[sx, sy]
    
    # Expand to find cell bounds
    # Find right edge
    right = start_x
    while right < min(start_x + max_size, width - 1):
        if color_distance(pixels[right, sy], start_color) > 50:
            break
        right += 1
    
    cell_width = right - start_x
    if cell_width < min_size or cell_width > max_size:
        return None
    
    return (start_x, start_y, cell_width, cell_width)  # Assume square

def extract_row_colors(image, row_cells):
    """Extract the color classification for each cell in a row."""
    pixels = image.load()
    colors = []
    
    for (x, y, w, h) in row_cells:
        # Sample from center of cell
        cx = x + w // 2
        cy = y + h // 2
        
        # Average a small area for robustness
        samples = []
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                sx = max(0, min(cx + dx, image.size[0] - 1))
                sy = max(0, min(cy + dy, image.size[1] - 1))
                samples.append(pixels[sx, sy])
        
        # Average color
        avg_r = sum(s[0] for s in samples) // len(samples)
        avg_g = sum(s[1] for s in samples) // len(samples)
        avg_b = sum(s[2] for s in samples) // len(samples)
        
        colors.append(classify_color((avg_r, avg_g, avg_b)))
    
    return colors

def process_wordle_screenshot(image, word):
    """
    Process a Wordle screenshot and return parsed dict.
    
    Args:
        image: PIL Image of Wordle screenshot
        word: The word that was guessed (5 letters)
    
    Returns:
        parsed dict compatible with find_best_suggestions()
    """
    word = word.upper().strip()
    
    if len(word) != 5:
        return None
    
    # Find the grid
    rows = find_wordle_grid(image)
    
    if not rows:
        # Fallback: try simpler detection
        colors = detect_colors_simple(image, word)
    else:
        # Get colors from the LAST filled row
        colors = extract_row_colors(image, rows[-1])
    
    if not colors or len(colors) != 5:
        return None
    
    # Build parsed dict
    green = {}
    yellow = {}
    grey = set()
    
    for i, (letter, color) in enumerate(zip(word, colors)):
        if color == "green":
            green[i] = letter
        elif color == "yellow":
            yellow[i] = letter
        else:  # grey
            grey.add(letter)
    
    # Clean up grey
    grey = grey - set(yellow.values()) - set(green.values())
    
    return {
        "word": word,
        "green": green,
        "yellow": yellow,
        "grey": grey,
        "extra_count": 0
    }

def detect_colors_simple(image, word):
    """
    Simpler fallback: sample colors at expected grid positions.
    Assumes standard Wordle layout.
    """
    width, height = image.size
    pixels = image.load()
    
    # Estimate grid position (centered horizontally, upper third)
    grid_width = width * 0.5
    cell_size = grid_width / 5
    start_x = (width - grid_width) / 2
    
    # Try a few y positions
    for y_ratio in [0.25, 0.30, 0.35, 0.40, 0.45]:
        y = int(height * y_ratio)
        colors = []
        
        for i in range(5):
            cx = int(start_x + cell_size * i + cell_size / 2)
            cy = y
            
            if 0 <= cx < width and 0 <= cy < height:
                color = pixels[cx, cy]
                colors.append(classify_color(color))
        
        # Check if we got valid colors (not all grey/background)
        if len(colors) == 5 and any(c in ("green", "yellow") for c in colors):
            return colors
    
    return None

def has_image(request_values):
    """Check if the message contains an image."""
    num_media = int(request_values.get("NumMedia", 0))
    return num_media > 0

def get_image_url(request_values):
    """Get the image URL from the request."""
    return request_values.get("MediaUrl0")

def extract_word_from_message(text):
    """Extract a 5-letter word from message text."""
    # Remove common prefixes/suffixes
    text = text.strip().upper()
    
    # Find any 5-letter alphabetic sequence
    match = re.search(r'\b([A-Z]{5})\b', text)
    if match:
        return match.group(1)
    
    # Try without word boundaries
    match = re.search(r'([A-Z]{5})', text.replace(' ', ''))
    if match:
        return match.group(1)
    
    return None


# ============================================
# PATTERN PARSER - PARENTHESES FOR YELLOW
# ============================================

def parse_input(raw_input: str) -> dict:
    """
    Parse input:
    - UPPERCASE = green (locked in position)
    - (letter) = yellow (in word, not this position)
    - lowercase = grey (not in word)
    
    Returns: {
        "word": the 5-letter word (uppercase),
        "green": {pos: letter},
        "yellow": {pos: letter},
        "grey": set of letters,
        "extra_count": from + suffix
    }
    """
    # Check for + suffix
    clean_input = raw_input.rstrip('+')
    plus_count = len(raw_input) - len(clean_input)
    extra_count = plus_count * 3
    
    green = {}
    yellow = {}
    grey = set()
    word_chars = []
    
    pos = 0
    i = 0
    
    while i < len(clean_input):
        ch = clean_input[i]
        
        # Check for (letter) pattern - YELLOW
        if ch == '(' and i + 2 < len(clean_input) and clean_input[i + 2] == ')':
            letter = clean_input[i + 1].upper()
            word_chars.append(letter)
            yellow[pos] = letter
            pos += 1
            i += 3
            continue
        
        if ch.isalpha():
            letter = ch.upper()
            word_chars.append(letter)
            
            if ch.isupper():
                # GREEN - locked
                green[pos] = letter
            else:
                # GREY - not in word
                grey.add(letter)
            
            pos += 1
        
        i += 1
    
    word = "".join(word_chars)
    
    # Remove yellow/green letters from grey
    yellow_letters = set(yellow.values())
    green_letters = set(green.values())
    grey = grey - yellow_letters - green_letters
    
    return {
        "word": word,
        "green": green,
        "yellow": yellow,
        "grey": grey,
        "extra_count": extra_count
    }


# ============================================
# REGEX + PROBABILITY ENGINE
# ============================================

def filter_candidates(parsed: dict) -> list:
    """Filter words using regex and letter rules."""
    wordlist = load_wordlist()
    past = get_past_answers()
    available = wordlist - past
    
    green = parsed["green"]
    yellow = parsed["yellow"]
    grey = parsed["grey"]
    
    # Build regex for green letters
    pattern_chars = []
    for i in range(5):
        if i in green:
            pattern_chars.append(green[i])
        else:
            pattern_chars.append(".")
    
    regex_pattern = "".join(pattern_chars)
    pattern = re.compile(f"^{regex_pattern}$")
    
    yellow_letters = set(yellow.values())
    
    matches = []
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
        for pos, letter in yellow.items():
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


def find_best_suggestions(parsed: dict) -> list:
    """Return ALL suggestions ranked by probability."""
    candidates = filter_candidates(parsed)
    
    if not candidates:
        return []
    
    green = parsed["green"]
    yellow = parsed["yellow"]
    grey = parsed["grey"]
    
    # Score candidates
    position_freq = analyze_position_frequencies(candidates, green)
    tested = grey | set(yellow.values()) | set(green.values())
    
    scored = []
    for candidate in candidates:
        s = score_suggestion(candidate, position_freq, tested, green)
        scored.append((candidate, s))
    
    scored.sort(key=lambda x: -x[1])
    
    return [w for w, s in scored]


def build_pattern_display(parsed: dict) -> str:
    """Build visual pattern like [S]_(A)__"""
    green = parsed["green"]
    yellow = parsed["yellow"]
    
    display = []
    for i in range(5):
        if i in green:
            display.append(f"[{green[i]}]")
        elif i in yellow:
            display.append(f"({yellow[i]})")
        else:
            display.append("_")
    
    return "".join(display)


# ============================================
# BEST STARTER WORD
# ============================================

def get_recent_answers(n: int = 20) -> list:
    """Get the N most recent Wordle answers."""
    db = load_local_db()
    if not db:
        return []
    
    sorted_words = sorted(db.items(), key=lambda x: x[1].get("date", ""), reverse=True)
    return [word for word, _ in sorted_words[:n]]


def get_best_starter() -> dict:
    """Find best starting words - high freq letters, dissimilar to recent."""
    wordlist = load_wordlist()
    past = get_past_answers()
    available = wordlist - past
    
    if not available:
        return {"word": "SLATE", "letters": "S, L, A, T, E", "all": ["SLATE"]}
    
    recent = get_recent_answers(20)
    recent_letters = Counter()
    for word in recent:
        for ch in word:
            recent_letters[ch] += 1
    
    letter_freq = Counter()
    for word in available:
        for ch in set(word):
            letter_freq[ch] += 1
    
    # Score ALL words with 5 unique letters
    scored = []
    for word in available:
        if len(set(word)) != 5:
            continue
        
        score = 0
        for ch in word:
            score += letter_freq.get(ch, 0)
            score -= recent_letters.get(ch, 0) * 2
        
        scored.append((word, score))
    
    scored.sort(key=lambda x: -x[1])
    all_starters = [w for w, s in scored]
    
    if not all_starters:
        return {"word": "SLATE", "letters": "S, L, A, T, E", "all": ["SLATE"]}
    
    best = all_starters[0]
    return {"word": best, "letters": ", ".join(best), "all": all_starters}


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
    if len(word) != 5 or not word.isalpha():
        return {"error": True, "message": f"'{word}' isn't a valid 5-letter word."}

    result = check_via_api(word)
    if result is not None:
        return result

    result = check_via_local(word)
    if result is not None:
        return result

    return {"error": True, "message": "Service temporarily unavailable."}


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
            "*📸 Screenshot mode:*\n"
            "Send screenshot + word\n"
            "(e.g., image + 'slate')\n\n"
            "*⌨️ Text mode:*\n"
            "• *UPPERCASE* = 🟩 green\n"
            "• *(letter)* = 🟨 yellow\n"
            "• *lowercase* = ⬜ grey\n"
            "Example: *st(a)Re*\n\n"
            "*?* = best starter\n"
            "*+* = more results\n"
            "*** = reset\n\n"
            "Pure math. 📊"
        )
        return str(resp)
    
    # ========================================
    # IMAGE HANDLING
    # ========================================
    if has_image(request.values) and PIL_AVAILABLE:
        image_url = get_image_url(request.values)
        word = extract_word_from_message(incoming_msg)
        
        # If no word provided, check session or ask
        if not word:
            session = _user_sessions.get(from_number)
            if session and session.get("awaiting_word"):
                # They sent image without word after we asked
                msg.body("📸 Got your screenshot!\n\nWhat word did you play? Just send the 5 letters.")
                _user_sessions[from_number] = {"awaiting_word": True, "image_url": image_url}
                return str(resp)
            else:
                msg.body("📸 Got your screenshot!\n\nWhat word did you play? Just send the 5 letters.")
                _user_sessions[from_number] = {"awaiting_word": True, "image_url": image_url}
                return str(resp)
        
        # We have image and word - process it
        image = fetch_image_from_url(image_url)
        if not image:
            msg.body("⚠️ Couldn't load image. Try again or use text mode:\nst(a)Re")
            return str(resp)
        
        parsed = process_wordle_screenshot(image, word)
        if not parsed:
            msg.body(f"⚠️ Couldn't read the colors for *{word}*.\n\nTry text mode instead:\nExample: st(a)Re")
            return str(resp)
        
        # Success! Use existing engine
        all_suggestions = find_best_suggestions(parsed)
        show_count = 3
        
        _user_sessions[from_number] = {
            "suggestions": all_suggestions,
            "index": show_count,
            "parsed": parsed
        }
        
        pattern_display = build_pattern_display(parsed)
        candidate_count = len(all_suggestions)
        
        msg_parts = [f"📸 *{word}* → {pattern_display}"]
        
        if candidate_count > 0:
            msg_parts.append(f"📊 {candidate_count} possible")
            top = all_suggestions[:show_count]
            formatted = ", ".join(f"*{w}*" for w in top)
            msg_parts.append(f"💡 Best: {formatted}")
            if candidate_count > show_count:
                msg_parts.append("📌 Send *+* for more")
        else:
            msg_parts.append("⚠️ No matches. Check the image or try text mode.")
        
        msg.body("\n".join(msg_parts))
        return str(resp)
    
    # Check if they're responding to "what word?" prompt
    if from_number in _user_sessions:
        session = _user_sessions[from_number]
        if session.get("awaiting_word") and session.get("image_url"):
            word = extract_word_from_message(incoming_msg) or incoming_msg.strip().upper()
            
            if len(word) == 5 and word.isalpha():
                image = fetch_image_from_url(session["image_url"])
                if image:
                    parsed = process_wordle_screenshot(image, word)
                    if parsed:
                        # Clear awaiting state
                        all_suggestions = find_best_suggestions(parsed)
                        show_count = 3
                        
                        _user_sessions[from_number] = {
                            "suggestions": all_suggestions,
                            "index": show_count,
                            "parsed": parsed
                        }
                        
                        pattern_display = build_pattern_display(parsed)
                        candidate_count = len(all_suggestions)
                        
                        msg_parts = [f"📸 *{word}* → {pattern_display}"]
                        
                        if candidate_count > 0:
                            msg_parts.append(f"📊 {candidate_count} possible")
                            top = all_suggestions[:show_count]
                            formatted = ", ".join(f"*{w}*" for w in top)
                            msg_parts.append(f"💡 Best: {formatted}")
                            if candidate_count > show_count:
                                msg_parts.append("📌 Send *+* for more")
                        else:
                            msg_parts.append("⚠️ No matches found.")
                        
                        msg.body("\n".join(msg_parts))
                        return str(resp)
                
                # Fallback
                msg.body("⚠️ Couldn't process. Try text mode:\nst(a)Re")
                del _user_sessions[from_number]
                return str(resp)
    
    # Reset
    if incoming_msg.strip() == "*":
        if from_number in _user_sessions:
            del _user_sessions[from_number]
        msg.body("🔄 Reset! Send a word or *?* for best starter.")
        return str(resp)
    
    # Best starter
    if incoming_msg.strip() == "?":
        starter = get_best_starter()
        
        # Store all starters for + follow-up
        _user_sessions[from_number] = {
            "suggestions": starter["all"],
            "index": 1,
            "mode": "starter"
        }
        
        remaining = len(starter["all"]) - 1
        reply = f"🎯 *Best opener: {starter['word']}*\n📊 Letters: {starter['letters']}"
        if remaining > 0:
            reply += f"\n📌 Send *+* for more options"
        
        msg.body(reply)
        return str(resp)
    
    # More results (+, ++, +++)
    if incoming_msg.strip('+') == '':
        plus_count = len(incoming_msg.strip())
        
        session = _user_sessions.get(from_number)
        if not session or not session.get("suggestions"):
            msg.body("No previous search. Send a word first!")
            return str(resp)
        
        suggestions = session["suggestions"]
        current_index = session.get("index", 3)
        count = plus_count * 3
        
        next_batch = suggestions[current_index:current_index + count]
        
        if not next_batch:
            msg.body("No more suggestions for this pattern.")
            return str(resp)
        
        # Update index
        _user_sessions[from_number]["index"] = current_index + count
        
        formatted = ", ".join(f"*{w}*" for w in next_batch)
        remaining = len(suggestions) - (current_index + count)
        
        reply = f"💡 More: {formatted}"
        if remaining > 0:
            reply += f"\n📌 {remaining} left. Send *+* for more"
        
        msg.body(reply)
        return str(resp)
    
    # Parse input
    parsed = parse_input(incoming_msg)
    
    if len(parsed["word"]) != 5:
        msg.body("⚠️ Need exactly 5 letters.\nExample: st(a)Re")
        return str(resp)
    
    # Check if word was used
    result = check_wordle_word(parsed["word"])
    
    # Get ALL suggestions
    all_suggestions = find_best_suggestions(parsed)
    
    # How many to show
    show_count = 3 + parsed["extra_count"]
    
    # Store session
    _user_sessions[from_number] = {
        "suggestions": all_suggestions,
        "index": show_count,
        "parsed": parsed
    }
    
    # Build response
    msg_parts = []
    
    if result.get("error"):
        msg_parts.append(f"⚠️ {result['message']}")
    elif result["found"]:
        msg_parts.append(f"✅ *{result['word']}* was #{result['game']} ({result['date']})")
    else:
        msg_parts.append(f"❌ *{result['word']}* not used yet")
    
    pattern_display = build_pattern_display(parsed)
    candidate_count = len(all_suggestions)
    
    if candidate_count > 0:
        msg_parts.append(f"\n🎯 {pattern_display}")
        msg_parts.append(f"📊 {candidate_count} possible")
        
        top = all_suggestions[:show_count]
        formatted = ", ".join(f"*{w}*" for w in top)
        msg_parts.append(f"💡 Best: {formatted}")
        
        if candidate_count > show_count:
            msg_parts.append("📌 Send *+* for more")
    else:
        msg_parts.append("\n⚠️ No matches found. Check your input.")
    
    msg.body("\n".join(msg_parts))
    return str(resp)


@app.route("/", methods=["GET"])
def health():
    return "🟩 Wordle Solver running!", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
