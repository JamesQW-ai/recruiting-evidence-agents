#!/usr/bin/env python3
"""Emit an ephemeral local transcription for D13 material-intake checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import resource_limits

def emit(payload: dict[str, object], code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return code


def version_of(binary: str) -> str:
    try:
        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return "Not Provided"
    text = (result.stdout or result.stderr).strip().splitlines()
    return text[0] if result.returncode == 0 and text else "Not Provided"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Transcribe one audio file using a local whisper.cpp backend without retaining files."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--engine", choices=["whisper-cpp"], default="whisper-cpp")
    parser.add_argument("--engine-bin", default="whisper-cli")
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--language", default="zh")
    args = parser.parse_args()

    if not args.input.is_file():
        return emit({"status": "Blocked - Audio File Not Found", "input": str(args.input)}, 2)
    if not args.model.is_file():
        return emit({"status": "Blocked - ASR Model Not Found", "model": str(args.model)}, 3)
    if shutil.which(args.engine_bin) is None:
        return emit(
            {"status": "Blocked - ASR Backend Not Available", "engine_bin": args.engine_bin}, 4
        )
    if shutil.which("ffmpeg") is None:
        return emit({"status": "Blocked - Audio Decoder Not Available", "decoder": "ffmpeg"}, 5)
    try:
        resource_limits.require_audio_file_within_limit(args.input)
    except SystemExit as error:
        return emit({"status": "Blocked - Audio Resource Limit", "reason": str(error)}, 8)

    digest = hashlib.sha256()
    with args.input.open("rb") as stream:
        for block in iter(lambda: stream.read(resource_limits.STREAM_CHUNK_BYTES), b""):
            digest.update(block)
    source_hash = digest.hexdigest()
    with tempfile.TemporaryDirectory(prefix="recruiting-evidence-asr-") as temp_dir:
        temp_path = Path(temp_dir)
        normalized = temp_path / "normalized.wav"
        output_prefix = temp_path / "transcript"
        decode = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-i",
                str(args.input),
                "-ac",
                "1",
                "-ar",
                "16000",
                str(normalized),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if decode.returncode != 0:
            return emit(
                {
                    "status": "Blocked - Audio Decode Failed",
                    "audio_sha256": source_hash,
                    "temporary_artifacts_deleted": True,
                },
                6,
            )

        transcribe = subprocess.run(
            [
                args.engine_bin,
                "-m",
                str(args.model),
                "-f",
                str(normalized),
                "-l",
                args.language,
                "-otxt",
                "-of",
                str(output_prefix),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        transcript_path = output_prefix.with_suffix(".txt")
        if transcribe.returncode != 0 or not transcript_path.is_file():
            return emit(
                {
                    "status": "Blocked - Audio Transcription Failed",
                    "audio_sha256": source_hash,
                    "engine": args.engine,
                    "engine_version": version_of(args.engine_bin),
                    "temporary_artifacts_deleted": True,
                },
                7,
            )

        transcript = transcript_path.read_text(encoding="utf-8").strip()
        return emit(
            {
                "status": "Transcribed",
                "audio_sha256": source_hash,
                "engine": args.engine,
                "engine_version": version_of(args.engine_bin),
                "language": args.language,
                "model": args.model.name,
                "transcript": transcript,
                "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
                "temporary_artifacts_deleted": True,
            }
        )


if __name__ == "__main__":
    raise SystemExit(main())
