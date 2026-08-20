# J.A.R.V.I.S — Local LLM Setup (Optional / Disabled by Default)

Optional local/offline AI configuration for JARVIS.

> **Current v1.1 status:**  
> JARVIS v1.1 does **not** use a local LLM by default. This guide is kept for future optional offline use.

---

## Prerequisites

* Windows 10/11
* Python 3.11+
* Ollama installed
* A local model such as `llama3.1:8b` or `qwen2.5:7b`

Install Ollama from:

https://ollama.com/download

---

## Install a Model

Open PowerShell and run:

```powershell
ollama pull llama3.1:8b
```

You can replace the model with any supported Ollama model.

---

## Configure JARVIS

Edit `config/llm_config.json`:

```json
{
  "provider": "ollama",
  "model": "llama3.1:8b",
  "base_url": "http://localhost:11434"
}
```

---

## Start Ollama

```powershell
ollama serve
```

Keep this terminal running.

---

## Launch JARVIS

```powershell
python main.py
```

JARVIS will use the local model when offline mode is enabled.

---

## Recommended Models

| Model       | RAM      | Speed  | Quality   |
| ----------- | -------- | ------ | --------- |
| llama3.1:8b | 8–12 GB  | Fast   | Excellent |
| qwen2.5:7b  | 8–10 GB  | Fast   | Excellent |
| mistral:7b  | 8–10 GB  | Fast   | Very good |
| gemma2:9b   | 10–14 GB | Medium | Excellent |

---

## Troubleshooting

### Ollama not found

Ensure `ollama` is in your PATH.

### Connection refused

Run:

```powershell
ollama serve
```

### Model not installed

Run:

```powershell
ollama pull llama3.1:8b
```

---

## Notes

* Offline mode keeps conversations on your computer.
* Performance depends on your CPU, GPU, and available RAM.
* Voice recognition and text-to-speech remain local even when the LLM is local.

---

*Maintained for JARVIS v1.1 — August 2026.*
