# ============================================================
# CONFIGURATION
# ============================================================
#
# All runtime configuration comes from the environment / .env file.
# Secrets are never hardcoded.

import os
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

# Project root = parent of this server/ package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load <project-root>/.env (existing environment variables always win).
load_dotenv(PROJECT_ROOT / ".env")


# ------------------------------------------------------------
# Server
# ------------------------------------------------------------
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


# ------------------------------------------------------------
# Groq
# ------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is not set. Copy .env.example to .env and add your key."
    )

groq_client = Groq(api_key=GROQ_API_KEY)

# Whisper model
STT_MODEL = os.getenv("STT_MODEL", "whisper-large-v3-turbo")

# AI model
AI_MODEL = os.getenv("AI_MODEL", "openai/gpt-oss-20b")

# Text-to-speech model/voice. Groq TTS emits WAV only; MP3 is produced
# by re-encoding locally (see tts.transcode_wav_to_mp3).
# This model is the one active for this account, but the Groq console must
# accept the model terms before audio generation succeeds.
TTS_MODEL = os.getenv("TTS_MODEL", "canopylabs/orpheus-v1-english")

TTS_VOICE = os.getenv("TTS_VOICE", "autumn")

# "mp3" (default) or "wav" for the response audio sent to the Soundbox.
TTS_FORMAT = os.getenv("TTS_FORMAT", "mp3")

# Bitrate (kbps) for the re-encoded MP3. 64k mono is ample for speech.
TTS_MP3_BITRATE = int(os.getenv("TTS_MP3_BITRATE", "64"))


# ------------------------------------------------------------
# WebSocket protocol limits
# ------------------------------------------------------------
# Generous defaults: the Soundbox sends 8 KB chunks over cellular, so
# these only exist to reject clearly oversized/malicious payloads
# safely instead of crashing the server.

# Maximum size of a single binary WebSocket frame (bytes).
MAX_WS_FRAME_BYTES = int(os.getenv("MAX_WS_FRAME_BYTES", str(4 * 1024 * 1024)))

# Maximum accumulated audio request size (bytes).
MAX_AUDIO_SIZE_BYTES = int(os.getenv("MAX_AUDIO_SIZE_BYTES", str(16 * 1024 * 1024)))

# Maximum audio duration in seconds (checked after format validation).
MAX_AUDIO_DURATION_SECONDS = int(os.getenv("MAX_AUDIO_DURATION_SECONDS", "600"))

# Size of each binary frame used when streaming response audio back.
RESPONSE_CHUNK_SIZE = int(os.getenv("RESPONSE_CHUNK_SIZE", str(16 * 1024)))

# WebSocket heartbeat interval (seconds) used to detect stale clients.
HEARTBEAT_INTERVAL_SECONDS = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "30"))

# How long a client has to send its {"type": "start", "imei": ...}
# identification frame before the connection is closed.
IDENTIFICATION_TIMEOUT_SECONDS = int(os.getenv("IDENTIFICATION_TIMEOUT_SECONDS", "30"))
