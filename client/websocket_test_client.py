#!/usr/bin/env python3
"""
WebSocket test client for the AI Voice Support server.

Connects to /ws/voice, identifies with an IMEI, streams a real
MP3/WAV file as chunked binary frames, then receives the response
audio and saves it to disk.

Usage:

    python client/websocket_test_client.py audio/test_audio.mp3

Options:

    --url      ws://localhost:8000/ws/voice
    --imei     861185084364857
    --chunk    8192
    --out      response.mp3 (default: response.<format>)
    --timeout  120 seconds to wait for each server message
"""

import argparse
import json
import sys
import time
from pathlib import Path

try:
    from websockets.sync.client import connect
except ImportError:
    print("The 'websockets' package is required: pip install websockets")
    sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Test client for the AI Voice Support WebSocket server"
    )
    parser.add_argument(
        "audio_file",
        help="Path to a real MP3 or WAV file to stream to the server"
    )
    parser.add_argument(
        "--url",
        default="ws://localhost:8000/ws/voice",
        help="WebSocket URL (default: ws://localhost:8000/ws/voice)"
    )
    parser.add_argument(
        "--imei",
        default="861185084364857",
        help="Device IMEI to identify with (default: 861185084364857)"
    )
    parser.add_argument(
        "--chunk",
        type=int,
        default=8192,
        help="Binary chunk size in bytes (default: 8192)"
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output file for the response audio "
             "(default: response.<detected format>)"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for each server message (default: 120)"
    )
    return parser.parse_args()


def recv_json(ws, timeout, context):
    """Receive one text frame and parse it as JSON."""

    message = ws.recv(timeout=timeout)

    if isinstance(message, bytes):
        raise RuntimeError(
            f"Expected a JSON frame while {context}, "
            f"but received {len(message)} binary bytes"
        )

    return json.loads(message)


def main():
    args = parse_args()

    audio_path = Path(args.audio_file)

    if not audio_path.is_file():
        print(f"Audio file not found: {audio_path}")
        sys.exit(1)

    audio_bytes = audio_path.read_bytes()

    if not audio_bytes:
        print(f"Audio file is empty: {audio_path}")
        sys.exit(1)

    header = audio_bytes[:12]
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        detected = "wav"
    elif header[:3] == b"ID3" or (header[0] == 0xFF and (header[1] & 0xE0) == 0xE0):
        detected = "mp3"
    else:
        detected = "unknown"

    print("=" * 70)
    print("AI VOICE SUPPORT - WEBSOCKET TEST CLIENT")
    print("=" * 70)
    print(f"URL          : {args.url}")
    print(f"IMEI         : {args.imei}")
    print(f"Audio file   : {audio_path}")
    print(f"Audio size   : {len(audio_bytes) / 1024:.2f} KB ({detected})")
    print(f"Chunk size   : {args.chunk} bytes")
    print("=" * 70)

    try:
        ws = connect(
            args.url,
            open_timeout=15,
            close_timeout=10,
            max_size=None,
        )
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    print("[1] Connected")

    try:

        # ----------------------------------------------------
        # 2. Send IMEI
        # ----------------------------------------------------
        ws.send(json.dumps({"type": "start", "imei": args.imei}))
        print(f"[2] Sent start frame (IMEI {args.imei})")

        # ----------------------------------------------------
        # 3. Wait for ready
        # ----------------------------------------------------
        while True:
            frame = recv_json(ws, args.timeout, "waiting for ready")
            frame_type = frame.get("type")

            if frame_type == "ready":
                print(f"[3] Ready (IMEI {frame.get('imei')})")
                break

            if frame_type == "ping":
                ws.send(json.dumps({"type": "pong"}))
                continue

            if frame_type == "error":
                raise RuntimeError(f"Server error: {frame.get('message')}")

            print(f"    (ignoring frame while waiting for ready: {frame_type})")

        # ----------------------------------------------------
        # 4-6. Stream the file as binary frames
        # ----------------------------------------------------
        chunks = 0
        for offset in range(0, len(audio_bytes), args.chunk):
            ws.send(audio_bytes[offset:offset + args.chunk])
            chunks += 1
        print(f"[4-6] Sent {chunks} binary frames")

        # ----------------------------------------------------
        # 7. audio_end
        # ----------------------------------------------------
        ws.send(json.dumps({"type": "audio_end"}))
        print("[7] Sent audio_end")

        # ----------------------------------------------------
        # 8. response_start
        # ----------------------------------------------------
        while True:
            frame = recv_json(ws, args.timeout, "waiting for response_start")
            frame_type = frame.get("type")

            if frame_type == "response_start":
                audio_format = frame.get("format", "mp3")
                expected_size = frame.get("size", 0)
                print(f"[8] response_start: format={audio_format}, "
                      f"size={expected_size} bytes")
                break

            if frame_type == "text_response":
                text = frame.get("text", "")
                print("[8] Server could not produce audio (text fallback):")
                print(f"    {text}")
                ws.close()
                return

            if frame_type == "ping":
                ws.send(json.dumps({"type": "pong"}))
                continue

            if frame_type == "error":
                raise RuntimeError(f"Server error: {frame.get('message')}")

        # ----------------------------------------------------
        # 9. Receive binary response frames
        # ----------------------------------------------------
        response = bytearray()
        started = time.time()

        while True:

            message = ws.recv(timeout=args.timeout)

            if isinstance(message, bytes):
                response.extend(message)
                continue

            frame = json.loads(message)
            frame_type = frame.get("type")

            if frame_type == "response_end":
                print("[10] response_end")
                break

            if frame_type == "ping":
                ws.send(json.dumps({"type": "pong"}))
                continue

            if frame_type == "error":
                raise RuntimeError(f"Server error: {frame.get('message')}")

            print(f"    (ignoring frame during audio: {frame_type})")

        elapsed = time.time() - started

        # ----------------------------------------------------
        # 11. Save the response
        # ----------------------------------------------------
        out_path = Path(args.out) if args.out else Path(f"response.{audio_format}")
        out_path.write_bytes(response)

        print(f"[9] Received {len(response) / 1024:.2f} KB "
              f"in {elapsed:.2f} sec")
        print(f"[11] Saved response audio to: {out_path}")

        if expected_size and len(response) != expected_size:
            print(f"    WARNING: size mismatch! expected={expected_size} "
                  f"received={len(response)}")

    except Exception as e:
        print(f"FAILED: {e}")
        try:
            ws.close()
        except Exception:
            pass
        sys.exit(1)

    ws.close()
    print("Done.")


if __name__ == "__main__":
    main()
