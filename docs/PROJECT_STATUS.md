# J.A.R.V.I.S — Project Status (v1.0)

*Last updated: August 2026*

---

## ✅ Fully Working & Verified

- **Voice conversation** – real‑time speech‑to‑speech via Gemini Live.
- **Volume control** – precise levels set through `nircmd.exe`.
- **Brightness control** – works on laptops via `screen_brightness_control` + WMI fallback.
- **Window management** – minimize, maximize, close any visible window by its title.
- **Lock screen** – instant lock using the native Windows API.
- **Screenshots** – captured with `mss` and saved directly to the user’s Desktop.
- **File operations** – create, read, write, move, copy, rename, delete, and search files.
- **File conversion** – TXT → PDF (via `fpdf2`), DOCX → PDF (via `docx2pdf`).
- **Multi‑step agent** – research a topic, collect results, and write a full report to disk.
- **Browser control** – navigate, search, manage tabs (new, switch, close, list), scroll, reload, and extract text.
- **Long‑form reading** – reads complete files aloud without truncation.
- **Interrupt / Resume button** – dedicated ⏹ STOP button that instantly halts speech and processing; ▶ RESUME restores listening without reconnection.
- **Persistent memory** – structured user profile saved in `memory/long_term.json`.
- **System prompt & personality** – professional, concise, no “Thank you” loops, varied responses.

---

## ⚠️ Known Limitations (minor)

- **UI text lag** – long responses still animate character‑by‑character (instant display planned).
- **Offline mode** – less polished than online; lacks interrupt support and full tool parity.
- **Planner fallback** – occasionally produces a sub‑optimal plan when the primary LLM is rate‑limited.
- **Image resize / convert** – supported by `file_processor` but the model sometimes routes to `agent_task` instead; a stronger prompt rule can fix this.
- **Long‑conversation keep‑alive** – heartbeat is active, but under extreme idle the session may still drop (rate limit).

---

## 🚀 Launch

```bash
python -m pip install -r requirements.txt
python -m playwright install
python main.py