# ============================================================
# SPEECH TO TEXT
# ============================================================
#
# Provider/model/configuration carried over from the reference
# MQTT server.py: Groq Whisper, language "en", json response
# format, temperature 0.0.

import io
import logging

from . import config
from .audio import AUDIO_EXTENSIONS

logger = logging.getLogger("WS_AI_SERVER")


def transcribe_audio(audio_bytes, audio_format="mp3"):
    """
    Send the audio bytes directly to Groq Whisper.

    No temporary file is required. Whisper selects its decoder from
    the filename, so audio_format (as returned by validate_audio)
    decides the extension we attach to the in-memory file.
    """

    logger.info(
        "Sending %.2f KB of %s audio to Whisper",
        len(audio_bytes) / 1024,
        audio_format.upper()
    )

    audio_file = io.BytesIO(audio_bytes)

    # Give the in-memory file a filename matching the real format.
    extension = AUDIO_EXTENSIONS.get(audio_format, "mp3")
    audio_file.name = "soundbox_audio.{ext}".format(ext=extension)

    transcription = config.groq_client.audio.transcriptions.create(
        file=audio_file,
        model=config.STT_MODEL,

        # Change/remove this if your Soundbox supports
        # multiple spoken languages.
        language="en",

        response_format="json",

        temperature=0.0
    )

    text = transcription.text.strip()

    return text
