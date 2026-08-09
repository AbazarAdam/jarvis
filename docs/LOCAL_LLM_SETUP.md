Local LLM (Ollama) — Automated Setup

Jarvis now automatically installs, configures, and starts a local Ollama service on first run.

- No manual steps required. On first launch Jarvis will:
  1. Detect Ollama; if missing, download and install the appropriate package for your OS (may trigger a single elevation/UAC prompt on Windows).
  2. Pull the configured model (default: `llama3.2:3b`) once.
  3. Start the Ollama service and verify `http://localhost:11434` is responding.

- Behavior:
  - First run: shows setup progress in the UI and performs one-time install/pull (may take a few minutes for the model).
  - Subsequent runs: starts Ollama service quickly (no re-download / re-pull unless config changes).
  - If installation or pull fails, Jarvis falls back to cloud LLMs automatically and logs the error to `logs/llm_errors.log`.

You can still adjust `config/llm_config.json` to change model or disable local LLM.
