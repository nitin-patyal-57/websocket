# ============================================================
# TEXT TO SPEECH
# ============================================================
#
# Groq TTS + WAV -> MP3 transcoding carried over verbatim from the
# reference MQTT server.py: Groq emits WAV only, so the WAV is
# re-encoder locally with lameenc (preferred) or ffmpeg (fallback).
# If neither is available the WAV is used unchanged, so a missing
# encoder degrades payload size, never the response itself.

import io
import logging
import shutil
import subprocess
import threading
import wave

from . import config

logger = logging.getLogger("WS_AI_SERVER")

# Resolved once on first use: "lameenc", "ffmpeg" or None.
_mp3_encoder = None
_mp3_encoder_lock = threading.Lock()


def _resolve_mp3_encoder():
    """
    Pick an available MP3 encoder, once per process.
    """

    global _mp3_encoder

    if _mp3_encoder is not None:
        return _mp3_encoder

    with _mp3_encoder_lock:

        if _mp3_encoder is not None:
            return _mp3_encoder

        try:
            import lameenc  # noqa: F401
            _mp3_encoder = "lameenc"

        except ImportError:

            if shutil.which("ffmpeg"):
                _mp3_encoder = "ffmpeg"
            else:
                _mp3_encoder = "none"

        if _mp3_encoder == "none":

            logger.warning(
                "No MP3 encoder available (tried lameenc, ffmpeg). TTS responses "
                "will be sent as WAV. Install one with: pip install lameenc"
            )

        else:

            logger.info(
                "MP3 encoder: %s (%d kbps)",
                _mp3_encoder,
                config.TTS_MP3_BITRATE
            )

    return _mp3_encoder


def _transcode_with_lameenc(wav_bytes):

    import lameenc

    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:

        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        pcm = wav.readframes(wav.getnframes())

    if sample_width != 2:

        raise ValueError(
            "lameenc needs 16-bit PCM, got {n}-byte samples".format(
                n=sample_width
            )
        )

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(config.TTS_MP3_BITRATE)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(channels)
    encoder.set_quality(2)

    return encoder.encode(pcm) + encoder.flush()


def _transcode_with_ffmpeg(wav_bytes):

    process = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-f", "wav",
            "-i", "pipe:0",
            "-codec:a", "libmp3lame",
            "-b:a", "{k}k".format(k=config.TTS_MP3_BITRATE),
            "-f", "mp3",
            "pipe:1",
        ],
        input=wav_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30
    )

    if process.returncode != 0 or not process.stdout:

        raise RuntimeError(
            "ffmpeg failed: {err}".format(
                err=process.stderr.decode("utf-8", "replace").strip()
            )
        )

    return process.stdout


def transcode_wav_to_mp3(wav_bytes):
    """
    Re-encode WAV bytes to MP3.

    Returns:
        (bytes, "mp3") on success, or (wav_bytes, "wav") if no encoder
        is available or the encode failed - the caller sends
        whatever comes back, so this never loses a response.
    """

    encoder = _resolve_mp3_encoder()

    if encoder == "none":
        return wav_bytes, "wav"

    try:

        if encoder == "lameenc":
            mp3_bytes = _transcode_with_lameenc(wav_bytes)
        else:
            mp3_bytes = _transcode_with_ffmpeg(wav_bytes)

        if not mp3_bytes:
            raise RuntimeError("encoder returned no data")

        logger.info(
            "Transcoded TTS audio to MP3: %.2f KB -> %.2f KB (%.0f%% smaller)",
            len(wav_bytes) / 1024,
            len(mp3_bytes) / 1024,
            100 * (1 - len(mp3_bytes) / len(wav_bytes))
        )

        return mp3_bytes, "mp3"

    except Exception as e:

        logger.warning(
            "MP3 transcode failed (%s), sending WAV instead: %s",
            encoder,
            e
        )

        return wav_bytes, "wav"


def generate_response_audio(text):
    """
    Convert the text response into audio bytes, so the Soundbox
    receives something it can play directly - the device has no way to
    speak plain text on its own.

    Groq TTS only emits WAV, so the WAV is re-encoded locally to
    TTS_FORMAT (MP3 by default, matching the MP3 uploads on the
    request side).

    Returns:
        (audio_bytes, format) on success, or (None, None) if TTS
        generation failed - the caller falls back to sending a plain
        text response instead. The returned format is what was actually
        produced, which may be "wav" if no MP3 encoder is installed.
    """

    logger.info(
        "Generating TTS audio (%s / %s -> %s) for: %s",
        config.TTS_MODEL,
        config.TTS_VOICE,
        config.TTS_FORMAT,
        text
    )

    try:

        # Always request WAV: Groq rejects every other container with
        # "response_format must be one of [wav]".
        speech = config.groq_client.audio.speech.create(
            model=config.TTS_MODEL,
            voice=config.TTS_VOICE,
            input=text,
            response_format="wav"
        )

        wav_bytes = speech.read()

    except Exception as e:

        message = str(e)

        # The Groq account has not accepted this model's terms yet.
        # Nothing in the code can fix that, so log the action to take
        # instead of a stack trace the operator cannot act on.
        if "model_terms_required" in message or "requires terms acceptance" in message:

            logger.error(
                "TTS model %s requires terms acceptance. An org admin must accept them at "
                "https://console.groq.com/playground?model=%s , or set TTS_MODEL to a model "
                "the account already has access to. Falling back to plain text.",
                config.TTS_MODEL,
                config.TTS_MODEL.replace("/", "%2F")
            )

            return None, None

        logger.exception(
            "TTS generation failed, will fall back to plain text: %s",
            e
        )

        return None, None

    if config.TTS_FORMAT == "wav":
        return wav_bytes, "wav"

    return transcode_wav_to_mp3(wav_bytes)
