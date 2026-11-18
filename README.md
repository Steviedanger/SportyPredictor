# SportyPredictor

SportyPredictor reads public football tips once a day, keeps the picks that more than one site agrees on, and builds six accumulator tickets: **2**, **5**, **10**, **25+**, **50**, and **100+** combined odds. The slip is printed in the console and sent through a Telegram bot.

The process does not place bets and it does not log in to any site. If a source blocks the request or changes its page, that source is skipped and the rest of the run continues.

These are overlaps between published tips, not a promise of a result. 18+.

## What it does

1. Scrapes tips from Forebet, FreeSuperTips, Football Tips Today, Melbet's public tips pages, and Tip724.
2. Extracts 1X2, correct score, over/under goals, handicap, and corners.
3. Groups the same fixture across sites. A pick named by two or more sources is a consensus. A market with tips but no overlap becomes a fallback from the strongest single source.
4. Builds one ticket per odds tier. The 2, 5, and 10 slips prefer short, agreed 1X2 and modest totals. The 25+, 50, and 100+ slips add correct scores, corner lines, and high goal lines. A fixture is used once on a ticket.
5. Sends the slip to Telegram.

## Setup (PowerShell)

```powershell
cd C:\Users\mccar\Desktop\SportyPredictor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

If activation is blocked:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Edit `.env` and set:

- `TELEGRAM_BOT_TOKEN` from [@BotFather](https://t.me/BotFather)
- `TELEGRAM_CHAT_ID` for the chat that should receive the message

To find a chat id, send any message to your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `chat.id`.

## Run

Check the parsers and the odds ladder without using the network or Telegram:

```powershell
python main.py --self-test
```

Build today's slip now, and print it without sending:

```powershell
python main.py --now --dry-run
```

Build today's slip now and send it:

```powershell
python main.py --now
```

Leave the scheduler running. It scrapes the card, takes the earliest kickoff that has not started, and runs the whole pipeline one hour before that match. It will not send a second slip for the same local day. The next card is scanned at 08:00 local time (change that with `PLAN_HOUR`).

```powershell
python main.py
```

Start that window in the morning and leave it open. If no source publishes a kickoff, the run uses `DEFAULT_KICKOFF` (15:00 by default) and still fires one hour earlier.

## Notes

- Times are your computer's local time.
- Forebet, Tip724, and the others sometimes answer with HTTP 403. The log names the source and the pipeline carries on with whoever responded.
- Melbet's betting site is script-rendered, so the Melbet parser reads the public tips and guidebook pages rather than the live odds board.
- A restart after a successful send will not send again until the next day. Delete `data\last_run.txt` if you want another send the same day, or run `python main.py --now`.
