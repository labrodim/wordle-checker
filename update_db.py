#!/usr/bin/env python3
"""
update_db.py — Daily Wordle Database Updater
=============================================
Fetches all historical Wordle answers from the WordleHints API
and saves them to wordle_answers.json as a local backup.

Run this daily via cron:
  0 1 * * * cd /path/to/wordle-checker && python3 update_db.py

(Runs at 1:00 AM daily — Wordle resets at midnight ET)
"""

import json
import os
import requests
import time

API_BASE = "https://wordlehints.co.uk/wp-json/wordlehint/v1/answers"
DB_PATH = os.path.join(os.path.dirname(__file__), "wordle_answers.json")


def fetch_all_answers() -> dict:
    """Fetch all historical Wordle answers from the API."""
    all_words = {}
    page = 1
    per_page = 100

    print("📥 Fetching Wordle answers from WordleHints API...")

    while True:
        try:
            resp = requests.get(
                API_BASE,
                params={"page": page, "per_page": per_page},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])

            if not results:
                break

            for entry in results:
                word = entry.get("answer", "").upper()
                if word:
                    all_words[word] = {
                        "game": entry.get("game"),
                        "date": entry.get("date"),
                        "difficulty": entry.get("difficulty"),
                    }

            total = data.get("total", 0)
            fetched = page * per_page
            print(f"  Page {page}: got {len(results)} words ({min(fetched, total)}/{total})")

            if fetched >= total:
                break

            page += 1
            time.sleep(0.5)  # Be nice to the API

        except requests.RequestException as e:
            print(f"  ⚠️ Error on page {page}: {e}")
            break

    return all_words


def update_database():
    """Fetch all answers and save to local JSON file."""
    # Load existing DB
    existing = {}
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "r") as f:
            existing = json.load(f)
        print(f"📂 Existing database: {len(existing)} words")

    # Fetch from API
    new_words = fetch_all_answers()

    if not new_words:
        print("❌ No data fetched. Keeping existing database.")
        return

    # Merge (new data takes priority)
    existing.update(new_words)

    # Save
    with open(DB_PATH, "w") as f:
        json.dump(existing, f, indent=2, sort_keys=True)

    print(f"✅ Database updated: {len(existing)} total words saved to {DB_PATH}")

    # Show latest entry
    latest = max(existing.values(), key=lambda x: x.get("date", ""))
    print(f"📅 Most recent: Wordle #{latest['game']} on {latest['date']}")


if __name__ == "__main__":
    update_database()
