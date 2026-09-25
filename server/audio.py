# ============================================================
# AUDIO FORMAT DETECTION / VALIDATION
# ============================================================
#
# The Soundbox uploads MP3 (much smaller over a cellular link than
# PCM WAV). WAV is still accepted so older firmware keeps working -
# the payload header decides which one it is, the device never has to
# tell us.
#
# Logic carried over verbatim from the reference MQTT server.py.

import io
import logging
import wave

logger = logging.getLogger("WS_AI_SERVER")

# Maps a detected format to the filename extension / MIME type we
# hand to Groq Whisper. Whisper picks its decoder from the filename,
# so this has to be right.
AUDIO_EXTENSIONS = {
    "mp3": "mp3",
    "wav": "wav",
}

# MP3 bitrate table (kbps) for MPEG-1 Layer III, indexed by the
# 4-bit bitrate field of the frame header.
_MP3_BITRATES_V1_L3 = [
    None, 32, 40, 48, 56, 64, 80, 96,
    112, 128, 160, 192, 224, 256, 320, None
]

# MPEG-1 / MPEG-2 / MPEG-2.5 sample rates, by version and by the
# 2-bit sample-rate field.
_MP3_SAMPLE_RATES = {
    3: [44100, 48000, 32000, None],   # MPEG-1
    2: [22050, 24000, 16000, None],   # MPEG-2
    0: [11025, 12000, 8000, None],    # MPEG-2.5
}


def detect_audio_format(audio_bytes):
    """
    Sniff the payload and return "mp3", "wav" or None.

    Nothing here trusts a filename or a protocol field - only the
    bytes themselves:

        "RIFF" .... "WAVE"  -> wav
        "ID3"               -> mp3 (ID3v2 tagged)
        0xFF 0xEx/0xFx      -> mp3 (bare MPEG frame sync)
    """

    if not audio_bytes or len(audio_bytes) < 4:
        return None

    head = bytes(audio_bytes[:12])

    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"

    if head[:3] == b"ID3":
        return "mp3"

    # Bare MP3: 11 sync bits set, and the version/layer fields must
    # not be the "reserved" values.
    if head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:

        version = (head[1] >> 3) & 0x03
        layer = (head[1] >> 1) & 0x03

        if version != 1 and layer != 0:
            return "mp3"

    return None


def _find_mp3_frame_header(audio_bytes):
    """
    Return the offset of the first MPEG audio frame header, skipping
    any ID3v2 tag. Returns None if no frame sync is found.
    """

    offset = 0

    if audio_bytes[:3] == b"ID3" and len(audio_bytes) >= 10:

        # ID3v2 size is 4 synchsafe bytes (7 bits each).
        size = (
            (audio_bytes[6] & 0x7F) << 21
            | (audio_bytes[7] & 0x7F) << 14
            | (audio_bytes[8] & 0x7F) << 7
            | (audio_bytes[9] & 0x7F)
        )

        offset = 10 + size

    # Scan a bounded window for the frame sync rather than the whole
    # payload - a valid frame starts right after the tag.
    limit = min(len(audio_bytes) - 4, offset + 8192)

    while offset < limit:

        if (
            audio_bytes[offset] == 0xFF
            and (audio_bytes[offset + 1] & 0xE0) == 0xE0
        ):
            return offset

        offset += 1

    return None


def _log_mp3_details(audio_bytes):
    """
    Best-effort logging of MP3 parameters, mirroring what the WAV
    path logs. Parsing failures are not fatal - Whisper decodes the
    file either way, this is only for the operator watching logs.

    Returns the estimated duration in seconds (0.0 if unknown).
    """

    try:

        offset = _find_mp3_frame_header(audio_bytes)

        if offset is None:
            logger.info("MP3: no frame header found (tag-only or truncated?)")
            return 0.0

        h1 = audio_bytes[offset + 1]
        h2 = audio_bytes[offset + 2]
        h3 = audio_bytes[offset + 3]

        version = (h1 >> 3) & 0x03
        layer = (h1 >> 1) & 0x03

        bitrate = _MP3_BITRATES_V1_L3[(h2 >> 4) & 0x0F]
        sample_rate = _MP3_SAMPLE_RATES.get(version, [None] * 4)[(h2 >> 2) & 0x03]
        channel_mode = (h3 >> 6) & 0x03
        channels = 1 if channel_mode == 3 else 2

        # CBR estimate. Good enough for a sanity check on length.
        duration = (
            (len(audio_bytes) - offset) * 8 / (bitrate * 1000)
            if bitrate
            else 0
        )

        logger.info(
            "MP3:"
            " channels=%d,"
            " bitrate=%s kbps,"
            " sample_rate=%s,"
            " duration~%.2f sec",
            channels,
            bitrate,
            sample_rate,
            duration
        )

        return duration

    except Exception as e:

        logger.info(
            "MP3: header parse skipped (%s)",
            e
        )

        return 0.0


def _log_wav_details(audio_bytes):
    """
    Parse the WAV header for logging, and confirm it is a complete,
    readable RIFF/WAVE file.

    Returns True if the header parsed, False otherwise.
    """

    try:

        with wave.open(io.BytesIO(audio_bytes), "rb") as wav:

            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.getnframes()

            duration = (
                frames / sample_rate
                if sample_rate > 0
                else 0
            )

            logger.info(
                "WAV:"
                " channels=%d,"
                " sample_width=%d,"
                " sample_rate=%d,"
                " frames=%d,"
                " duration=%.2f sec",
                channels,
                sample_width,
                sample_rate,
                frames,
                duration
            )

        return True

    except Exception as e:

        logger.error(
            "Invalid WAV data: %s",
            e
        )

        return False


def validate_audio(audio_bytes, max_duration_seconds=None):
    """
    Validate that the received payload contains a usable audio file.

    Returns:
        "mp3" / "wav" -> valid, and the format that was detected
        None          -> unusable payload
    """

    if not audio_bytes:
        logger.error("Received empty audio payload")
        return None

    audio_format = detect_audio_format(audio_bytes)

    if audio_format is None:

        logger.error(
            "Unrecognized audio payload (expected MP3 or WAV), first bytes: %s",
            bytes(audio_bytes[:12]).hex(" ")
        )

        return None

    duration = 0.0

    if audio_format == "wav":

        # WAV is fully parseable in the stdlib, so a bad header is
        # worth rejecting before we spend a Whisper call on it.
        if not _log_wav_details(audio_bytes):
            return None

        try:

            with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
                sample_rate = wav.getframerate()
                duration = (
                    wav.getnframes() / sample_rate
                    if sample_rate > 0
                    else 0
                )

        except Exception:
            duration = 0.0

    else:

        duration = _log_mp3_details(audio_bytes)

    if max_duration_seconds and duration and duration > max_duration_seconds:

        logger.error(
            "Audio duration %.2f sec exceeds limit of %d sec",
            duration,
            max_duration_seconds
        )

        return None

    return audio_format
