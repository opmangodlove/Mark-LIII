# ⚙️ MARK LIII — Personal JARVIS Voice Assistant

A real-time, hands-free voice AI for your computer — wake it with **"Hey Jarvis"**, talk naturally, and it can see your screen, hear and speak in real time, remember things across sessions, and control your PC.

Built on the **Gemini Live API** for native low-latency audio streaming.

---

## ✨ Highlights

| | |
|---|---|
| 🎙️ **Wake word** | Local "Hey Jarvis" detection — fully offline, mic never streams while asleep, auto-sleeps after silence |
| ⚡ **Real-time voice** | Ultra-low latency conversation in any language via Gemini Live |
| 👁️ **Vision** | Screen and webcam capture piped into your live session |
| 🖥️ **PC control** | Apps, volume, brightness, Wi-Fi, shortcuts, files, browser — all by voice |
| 🧠 **Memory** | Persistent, recallable long-term memory with a panel to view and delete facts |
| ↩️ **Undo** | Take back moves, renames, writes and setting changes |
| ⚠️ **Real confirmation** | Shutdown/restart/Wi-Fi wait for a button **you** press — the model can't confirm for you |
| 🧩 **Self-describing skills** | Every action/plugin declares itself in its own file — adding a skill is one file |
| ♾️ **Unlimited sessions** | Sliding-window context compression; reconnects keep the conversation |
| 🎨 **HUD** | PyQt6 interface: reactive waveform, live theming, voice picker, clipboard tools |
| 📊 **Dashboard** | Phone-friendly command center with task board (optional Supabase sync) |
| 🔊 **Audio hardening** | Junk-device filtering, verified mic adoption, Bluetooth A2DP-aware mic audit |

---

## ⚡ Quick Start

```bash
git clone https://github.com/opmangodlove/Mark-LIII.git
cd Mark-LIII
python setup.py      # installs dependencies for your OS
python main.py
```

On first launch, enter your **free Gemini API key** (stored locally in `config/api_keys.json`, never committed).

Optional: enable **⚙ → WAKE WORD** inside the app for a one-click local model download. Set up three-piece auth-free pairing to the dashboard from ⚙ → DASHBOARD.

---

## 📋 Requirements

| | |
|---|---|
| **OS** | Windows 10/11, macOS, Linux |
| **Python** | 3.11 – 3.14 |
| **Audio** | Microphone + speakers |
| **API key** | Free tier [Google AI Studio](https://aistudio.google.com/) key |
| **Wake word** *(optional)* | openwakeword, few MB, fully local |

---

## 🗂️ Structure

```
Mark-LIII/
├── main.py          # Gemini Live session, audio I/O, wake/sleep, tool dispatch
├── ui.py            # PyQt6 HUD
├── actions/         # Bundled skills (each self-describes via TOOL dict)
├── plugins/         # Drop-in skills (PLUGIN dict + run())
├── core/            # Wake word, mic health, audio devices, undo, confirm
├── memory/          # Persistent memory, task board, optional Supabase sync
├── dashboard/       # Command-center web UI + server
└── voice-launcher/  # Standalone wake-word listener ("Jarvis good morning")
```

---

## ⚠️ License

Personal and non-commercial use only — **[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)**.

Based on [Mark-LIII](https://github.com/FatihMakes/Mark-LIII) by FatihMakes.
