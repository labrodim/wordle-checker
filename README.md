# 🟩 Wordle Word Checker — SMS & WhatsApp Service

Text any 5-letter word → instantly find out if it's been used in Wordle, which puzzle number, and on what date.

Share the number with family and friends — everyone can use it!

## How It Works

```
You text:  CRANE
Reply:     ✅ YES! CRANE was Wordle #567
           📅 Date: 2024-01-08
           😅 Difficulty: 4.2/10
```

```
You text:  PIZZA
Reply:     ❌ Nope! PIZZA has NOT been a Wordle answer yet.
           It might show up in a future puzzle! 🤞
```

**No database to maintain!** The app queries the free [WordleHints API](https://wordlehints.co.uk/wordle-past-answers/api/) in real-time, which updates daily with each new Wordle answer.

---

## Setup Guide (30 minutes)

### Step 1: Twilio Account

1. Go to [twilio.com](https://www.twilio.com/) and create a free account
2. Get your **Account SID** and **Auth Token** from the dashboard
3. For **SMS**: Buy a phone number (~$1.15/month, pennies per text)
4. For **WhatsApp**: Use the free Twilio Sandbox (perfect for family/friends)

### Step 2: Deploy the App

#### Option A: Railway (Easiest — Free Tier Available)

1. Go to [railway.app](https://railway.app/) and sign in with GitHub
2. Push this project to a GitHub repo
3. Click "New Project" → "Deploy from GitHub repo"
4. Railway auto-detects the Procfile and deploys
5. Note your app URL (e.g., `https://wordle-checker-xxxx.up.railway.app`)

#### Option B: Render (Also Easy — Free Tier)

1. Go to [render.com](https://render.com/) and sign in
2. New → Web Service → connect your GitHub repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Note your app URL

#### Option C: Run Locally with ngrok (For Testing)

```bash
# Install dependencies
pip install -r requirements.txt

# Start the app
python app.py

# In another terminal, expose it to the internet
ngrok http 5000
# Note the https://xxxxx.ngrok.io URL
```

### Step 3: Connect Twilio to Your App

#### For SMS:
1. In Twilio Console → Phone Numbers → your number → Messaging
2. Set "A message comes in" webhook to: `https://YOUR-APP-URL/sms`
3. Method: POST
4. Save

#### For WhatsApp (Recommended for Family/Friends):
1. In Twilio Console → Messaging → Try it out → Send a WhatsApp message
2. Follow the sandbox setup (send "join your-sandbox-code" from WhatsApp)
3. Set the webhook URL to: `https://YOUR-APP-URL/sms`
4. Share the sandbox join instructions with family and friends

### Step 4: Share with Everyone!

**For SMS:** Just share the phone number. They text a word, they get an answer.

**For WhatsApp Sandbox:** Send them these instructions:
> "Save this number: +1-415-xxx-xxxx. Open WhatsApp, send the message 'join your-sandbox-code', and you're in! Now just text any 5-letter word to check if it's been in Wordle."

**For WhatsApp Business (production):** Apply via Twilio for a proper WhatsApp Business number — no sandbox code needed, just save the number and text.

---

## Costs

| Item | Cost |
|------|------|
| Twilio SMS number | ~$1.15/month |
| SMS sent/received | ~$0.0079 each |
| WhatsApp (sandbox) | Free |
| WhatsApp (production) | ~$0.005/message |
| Hosting (Railway/Render) | Free tier available |

For a family of 10 checking a few words a day, you're looking at **under $2/month**.

---

## Customization Ideas

- Add a "STATS" command that returns fun stats (most common letters, hardest words, etc.)
- Add a "TODAY" command that gives a hint for today's Wordle (without spoiling it)
- Add a "RANDOM" command that suggests a good starting word
- Track which family members check the most words (competitive streak!)

---

## Tech Stack

- **Python / Flask** — lightweight web server
- **Twilio** — SMS and WhatsApp messaging API
- **WordleHints API** — free, daily-updated Wordle answer database
- **Gunicorn** — production WSGI server

## License

MIT — do whatever you want with it!
