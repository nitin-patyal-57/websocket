# AI Voice Support WebSocket

This project is a FastAPI-based WebSocket voice assistant backend for mobile clients. It accepts recorded audio from a client, transcribes it with Groq Whisper, processes the message through a training or AI response flow, converts the response to speech using Groq TTS, and streams the audio back over the WebSocket.

## Features

- WebSocket voice streaming
- WAV/MP3 validation
- Groq Whisper transcription
- AI response generation
- Groq TTS audio synthesis
- Local MP3 transcoding for mobile playback
- Android demo client

## Project structure

- `server/` — FastAPI server, config, AI logic, STT, TTS, and WebSocket handling
- `android-client/` — Android client app for recording and playback
- `client/` — simple Python WebSocket test client
- `audio/` — sample audio files

## Requirements

- Python 3.10+
- A Groq API key

Install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Environment variables

Create a `.env` file with:

```env
GROQ_API_KEY=your_key_here
HOST=0.0.0.0
PORT=8000
STT_MODEL=whisper-large-v3-turbo
AI_MODEL=openai/gpt-oss-20b
TTS_MODEL=canopylabs/orpheus-v1-english
TTS_VOICE=autumn
TTS_FORMAT=mp3
```

## Run the server

```powershell
cd "C:\Users\Abcom\Documents\websocket\AI-Voice-Support-WebSocket"
.\.venv\Scripts\Activate.ps1
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Then connect to:

```text
ws://localhost:8000/ws/voice
```

## Android client

The Android demo client is under `android-client/` and can be built with:

```powershell
cd android-client
.\gradlew.bat assembleDebug
```

## Notes

- This project expects a valid Groq account and required model terms acceptance where applicable.
- Mobile use over cellular requires a public tunnel such as ngrok, because local private LAN addresses like `192.168.x.x` are not reachable over mobile data.

## License

See `LICENSE`.
