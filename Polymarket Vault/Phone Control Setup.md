---
title: Phone Control Setup
type: how-to
tags: [polymarket, phone, shortcuts]
---

# Phone control — iOS Shortcuts

The bot reads `control.json` and writes `status.json` in **iCloud Drive → PolymarketBot**.
On the phone: **Files app → Browse → iCloud Drive → PolymarketBot**.

- `status.json` — tap to view live metrics (open, clean win rate, P&L, paused).
- `control.json` — what the shortcuts below overwrite to command the bot.

The bot applies changes within ~20 seconds (iCloud sync + next scan).

## Shortcut: "Pause Bot"
1. Shortcuts app → **+** (new shortcut).
2. Add action → **Text** → type exactly: `{"paused": true, "midline": null}`
3. Add action → **Save File**.
4. On Save File: **Ask Where to Save = OFF**; **Destination Path** = `PolymarketBot/control.json` (service: iCloud Drive); **Overwrite If File Exists = ON**.
5. Name it **Pause Bot**. (Optional: ⋯ → Add to Home Screen.)

## Shortcut: "Resume Bot"
Same as above, but the Text is: `{"paused": false, "midline": null}`

## Optional: midline toggles
- Disable midline: text `{"paused": false, "midline": false}`
- Enable midline:  text `{"paused": false, "midline": true}`
- `null` = leave midline as configured; `true`/`false` = force it.

## Notes
- First run asks permission to access iCloud/Files — allow it.
- Confirm it worked by opening `status.json` and checking `"paused"`.
- These are the only safe remote commands wired in (pause/resume, midline). No arbitrary commands — deliberate, for safety around trades.
