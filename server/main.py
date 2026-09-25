# ============================================================
# AI VOICE SUPPORT - WEBSOCKET SERVER
# ============================================================
#
# FastAPI WebSocket server for the ESP32 + SIM7600 payment
# Soundbox. Built from scratch as a WebSocket application; the
# application pipeline (audio validation -> STT -> transaction /
# training / AI -> TTS -> MP3) is carried over from the reference
# MQTT server.
#
# Endpoints:
#     GET  /health
#     WS   /ws/voice

import asyncio
import json
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect

from . import config
from .ai import generate_ai_response
from .audio import validate_audio
from .logging_utils import next_request_id, set_request_id, setup_logging
from .stt import transcribe_audio
from .transactions import (
    detect_transaction_query,
    format_transaction_announcement,
    format_transaction_summary,
    get_last_transaction,
)
from .training import TRAINING_BY_INTENT, SOUNDBOX_TRAINING_DATA, match_training_response
from .tts import generate_response_audio
from .websocket_manager import manager

logger = setup_logging()


# ============================================================
# PROCESS ONE REQUEST (synchronous pipeline)
# ============================================================
#
# Mirrors process_audio_request() from the reference MQTT server:
#
#     validate_audio()
#         ->
#     transcribe_audio()
#         ->
#     detect_transaction_query()
#         ->  transaction response
#     else match_training_response()
#         ->  trained response or generate_ai_response()
#         ->
#     generate_response_audio()
#
# Runs in a worker thread (asyncio.to_thread) so the FastAPI event
# loop is never blocked by STT / AI / TTS network calls.

def _build_payload(text):
    """TTS a response text; fall back to (None, None, text)."""

    audio_bytes, audio_format = generate_response_audio(text)

    if audio_bytes:

        logger.info(
            "Response audio: %.2f KB of %s",
            len(audio_bytes) / 1024,
            audio_format.upper()
        )

        return audio_bytes, audio_format, text

    logger.warning(
        "TTS unavailable, sending text response instead (%d chars)",
        len(text)
    )

    return None, None, text


def _build_error_payload(reason):
    """Same error wording as the reference server's publish_error()."""

    response = f"Sorry, I could not process your request. Reason: {reason}"

    # Keep response short for Soundbox.
    if len(response) > 200:
        response = response[:200]

    return _build_payload(response)


def process_request(imei, audio_bytes):
    """
    Run the full application pipeline for one audio request.

    Returns:
        (audio_bytes, audio_format, text)

        audio_bytes is None when TTS failed - `text` then carries the
        response (or error) that must be delivered as a text_response.
    """

    logger.info("=" * 70)
    logger.info(
        "Processing request for IMEI: %s",
        imei
    )

    logger.info(
        "Audio payload size: %.2f KB",
        len(audio_bytes) / 1024
    )

    try:

        # ----------------------------------------------------
        # 1. Validate audio (MP3 or WAV)
        # ----------------------------------------------------

        audio_format = validate_audio(
            audio_bytes,
            max_duration_seconds=config.MAX_AUDIO_DURATION_SECONDS
        )

        if audio_format is None:

            return _build_error_payload(
                "Invalid audio payload (expected MP3 or WAV)"
            )

        logger.info(
            "Audio format: %s",
            audio_format.upper()
        )

        # ----------------------------------------------------
        # 2. Speech-to-text
        # ----------------------------------------------------

        logger.info("STT started")

        transcript = transcribe_audio(
            audio_bytes,
            audio_format
        )

        logger.info(
            "TRANSCRIPT: %s",
            transcript
        )

        if not transcript:

            return _build_error_payload(
                "No speech detected"
            )

        # ----------------------------------------------------
        # 3. Real transaction data (last txn / summary)
        # ----------------------------------------------------
        #
        # Checked before the FAQ matcher below: "what was my last
        # transaction" is a data lookup, not a troubleshooting
        # question, so it should never be scored against LED
        # training phrases or sent to the LLM.

        txn_query = detect_transaction_query(transcript)

        if txn_query == "last_transaction":

            logger.info("Transaction query: last_transaction")

            ai_response = format_transaction_announcement(
                get_last_transaction(imei)
            )

        elif txn_query == "summary":

            logger.info("Transaction query: summary")

            ai_response = format_transaction_summary(
                imei,
                period="today"
            )

        else:

            # ------------------------------------------------
            # 4. AI processing (training-data assisted)
            # ------------------------------------------------

            trained_response, matched_intent, match_score = match_training_response(
                transcript
            )

            logger.info(
                "Training match: intent=%s, score=%.2f%s",
                matched_intent,
                match_score,
                " [CONFIDENT - using canonical response]" if trained_response else ""
            )

            if trained_response:
                # Confident match against known merchant phrasing: use the
                # already-reviewed canonical response directly, skipping
                # the LLM call entirely.
                ai_response = trained_response
            else:
                extra_context = (
                    TRAINING_BY_INTENT[matched_intent]["context"]
                    if matched_intent else None
                )

                ai_response = generate_ai_response(
                    transcript,
                    extra_context=extra_context
                )

        logger.info(
            "AI RESPONSE: %s",
            ai_response
        )

        if not ai_response:

            return _build_error_payload(
                "AI returned an empty response"
            )

        # ----------------------------------------------------
        # 5. Response audio (TTS -> WAV -> MP3)
        # ----------------------------------------------------

        logger.info("TTS started")

        return _build_payload(ai_response)

    except Exception:

        logger.exception(
            "Error processing request"
        )

        return _build_error_payload(
            "AI processing failed"
        )

    finally:

        logger.info(
            "Finished processing IMEI: %s",
            imei
        )

        logger.info("=" * 70)


# ============================================================
# WEBSOCKET PROTOCOL HELPERS
# ============================================================

async def _send_error(websocket: WebSocket, connection, message: str):
    """Send a protocol-level error frame."""

    logger.error(message)

    if connection is not None:
        await manager.send_json(connection, {
            "type": "error",
            "message": message,
        })
    else:
        try:
            await websocket.send_json({"type": "error", "message": message})
        except Exception:
            pass


async def _handle_audio_end(websocket, connection, buffer) -> bool:
    """
    Process the accumulated audio and stream the response back.

    Returns False if the connection was lost while responding.
    """

    audio_bytes = bytes(buffer)
    buffer.clear()

    if not audio_bytes:
        await _send_error(
            websocket, connection,
            "audio_end received with no audio data"
        )
        return connection is not None

    logger.info(
        "Audio received: %.2f KB",
        len(audio_bytes) / 1024
    )

    # Blocking STT / AI / TTS pipeline - off the event loop.
    audio, audio_format, text = await asyncio.to_thread(
        process_request,
        connection.imei,
        audio_bytes
    )

    if audio:

        logger.info(
            "Sending response audio (%s, %.2f KB)",
            audio_format.upper(),
            len(audio) / 1024
        )

        sent = await manager.send_audio(connection, audio, audio_format)

    else:

        sent = await manager.send_json(connection, {
            "type": "text_response",
            "text": text,
        })

    if sent:
        logger.info("Response sent")

    return sent


# ============================================================
# WEBSOCKET ENDPOINT
# ============================================================

async def _wait_for_identification(websocket: WebSocket) -> str | None:
    """
    Wait for the device's first frame: {"type": "start", "imei": ...}

    Returns the IMEI, or None (after sending an error) if the client
    never identified itself.
    """

    try:
        message = await asyncio.wait_for(
            websocket.receive(),
            timeout=config.IDENTIFICATION_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        try:
            await websocket.close(code=1008, reason="Identification timeout")
        except Exception:
            pass
        logger.warning(
            "No identification frame received within %d sec, closing",
            config.IDENTIFICATION_TIMEOUT_SECONDS
        )
        return None

    if message.get("type") == "websocket.disconnect":
        return None

    raw = message.get("text")

    if raw is None:
        # Binary frames are only valid after identification.
        await _send_error(
            websocket, None,
            "First frame must be a JSON start message with an IMEI"
        )
        try:
            await websocket.close(code=1008, reason="Identify first")
        except Exception:
            pass
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        await _send_error(websocket, None, "Invalid JSON frame")
        try:
            await websocket.close(code=1008, reason="Invalid JSON")
        except Exception:
            pass
        return None

    if data.get("type") != "start":
        await _send_error(
            websocket, None,
            "First frame must be {\"type\": \"start\", \"imei\": ...}"
        )
        try:
            await websocket.close(code=1008, reason="Identify first")
        except Exception:
            pass
        return None

    imei = str(data.get("imei") or "").strip()

    if not imei:
        await _send_error(websocket, None, "start frame is missing 'imei'")
        try:
            await websocket.close(code=1008, reason="Missing IMEI")
        except Exception:
            pass
        return None

    return imei


@asynccontextmanager
async def lifespan(app: FastAPI):
    heartbeat_task = None

    if config.HEARTBEAT_INTERVAL_SECONDS > 0:
        heartbeat_task = asyncio.create_task(manager.run_heartbeat())

    yield

    if heartbeat_task:
        heartbeat_task.cancel()


app = FastAPI(
    title="AI Voice Support WebSocket Server",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "AI Voice Support WebSocket Server",
    }


@app.websocket("/ws/voice")
async def voice(websocket: WebSocket):
    """
    Soundbox voice channel.

    Protocol:

        -> {"type": "start", "imei": "..."}
        <- {"type": "ready", "imei": "..."}

        -> <binary frame> x N        (raw MP3/WAV bytes, chunked)
        -> {"type": "audio_end"}
        <- {"type": "response_start", "format": "mp3", "size": 12345}
        <- <binary frame> x N        (response audio)
        <- {"type": "response_end"}

        ... connection stays open for more requests ...
    """

    await websocket.accept()

    req_id = next_request_id()
    set_request_id(req_id)

    client = websocket.client
    logger.info(
        "WebSocket connected (%s:%s)",
        client.host if client else "?",
        client.port if client else "?"
    )

    connection = None
    buffer = bytearray()

    try:

        # ----------------------------------------------------
        # Identification
        # ----------------------------------------------------

        imei = await _wait_for_identification(websocket)

        if imei is None:
            return

        set_request_id(req_id)

        logger.info("IMEI: %s", imei)

        connection = await manager.register(websocket, imei)

        await manager.send_json(connection, {
            "type": "ready",
            "imei": imei,
        })

        logger.info("Client ready")

        # ----------------------------------------------------
        # Main loop: keep-alive request/response cycles
        # ----------------------------------------------------

        while True:

            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            connection.touch()

            # --------------------------------------------
            # Binary frame -> accumulate audio chunk
            # --------------------------------------------
            if message.get("bytes") is not None:

                chunk = message["bytes"]

                logger.debug("Audio chunk: %d bytes", len(chunk))

                if len(chunk) > config.MAX_WS_FRAME_BYTES:

                    await _send_error(
                        websocket, connection,
                        f"Binary frame of {len(chunk)} bytes exceeds "
                        f"MAX_WS_FRAME_BYTES ({config.MAX_WS_FRAME_BYTES})"
                    )
                    await websocket.close(code=1009, reason="Frame too large")
                    break

                if len(buffer) + len(chunk) > config.MAX_AUDIO_SIZE_BYTES:

                    await _send_error(
                        websocket, connection,
                        f"Audio payload exceeds "
                        f"MAX_AUDIO_SIZE_BYTES ({config.MAX_AUDIO_SIZE_BYTES})"
                    )
                    await websocket.close(code=1009, reason="Audio too large")
                    break

                if not buffer:
                    logger.info("Receiving audio...")

                buffer.extend(chunk)

                continue

            # --------------------------------------------
            # JSON control frame
            # --------------------------------------------
            raw = message.get("text")

            if raw is None:
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await _send_error(websocket, connection, "Invalid JSON frame")
                continue

            frame_type = data.get("type")

            if frame_type == "start":

                # Device re-sent identification: acknowledge again.
                requested_imei = str(data.get("imei") or "").strip()

                if requested_imei and requested_imei != connection.imei:
                    await _send_error(
                        websocket, connection,
                        "IMEI mismatch on an established connection"
                    )
                    continue

                await manager.send_json(connection, {
                    "type": "ready",
                    "imei": connection.imei,
                })

            elif frame_type == "audio_end":

                alive = await _handle_audio_end(
                    websocket, connection, buffer
                )

                if not alive:
                    break

            elif frame_type == "ping":

                # Liveness reply for server-initiated heartbeats.
                await manager.send_json(connection, {"type": "pong"})

            elif frame_type == "pong":
                # Client answered our heartbeat - nothing to do,
                # touch() already refreshed last_seen above.
                pass

            else:

                await _send_error(
                    websocket, connection,
                    f"Unknown frame type: {frame_type!r}"
                )

    except (WebSocketDisconnect, RuntimeError):

        # Normal client disconnect (or receive-after-disconnect).
        pass

    except Exception:

        logger.exception("WebSocket handler error")

    finally:

        if connection is not None:
            await manager.disconnect(connection)
            set_request_id(req_id)
            logger.info("Client disconnected (IMEI %s)", connection.imei)
        else:
            logger.info("Client disconnected (unidentified)")


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info("")
    logger.info("=" * 70)
    logger.info("SOUNDBOX WEBSOCKET AI SERVER")
    logger.info("=" * 70)

    logger.info("Listen          : ws://%s:%d/ws/voice", config.HOST, config.PORT)
    logger.info("Health          : http://%s:%d/health", config.HOST, config.PORT)
    logger.info("STT Model       : %s", config.STT_MODEL)
    logger.info("AI Model        : %s", config.AI_MODEL)
    logger.info("TTS             : %s / %s -> %s (%d kbps)",
                config.TTS_MODEL, config.TTS_VOICE,
                config.TTS_FORMAT, config.TTS_MP3_BITRATE)
    logger.info("Training data   : %d examples across %d intents",
                len(SOUNDBOX_TRAINING_DATA), len(TRAINING_BY_INTENT))
    logger.info("Max audio size  : %d bytes", config.MAX_AUDIO_SIZE_BYTES)
    logger.info("Max frame size  : %d bytes", config.MAX_WS_FRAME_BYTES)
    logger.info("Max duration    : %d sec", config.MAX_AUDIO_DURATION_SECONDS)
    logger.info("Response chunk  : %d bytes", config.RESPONSE_CHUNK_SIZE)
    logger.info("=" * 70)

    uvicorn.run(
        "server.main:app",
        host=config.HOST,
        port=config.PORT,
        log_level=config.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
