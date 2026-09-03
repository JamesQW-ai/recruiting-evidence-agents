#!/usr/bin/env python3
"""Focused contract checks for the ephemeral transcription wrapper."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "transcribe_audio.py"


class TranscriptionWrapperTests(unittest.TestCase):
    def test_missing_model_is_an_explicit_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "sample.mp3"
            audio.write_bytes(b"not an audio file")
            result = subprocess.run(
                ["python3", str(SCRIPT), "--input", str(audio), "--model", str(Path(temp_dir) / "missing.bin")],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["status"], "Blocked - ASR Model Not Found")

    def test_missing_input_is_an_explicit_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                ["python3", str(SCRIPT), "--input", str(Path(temp_dir) / "missing.mp3"), "--model", __file__],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "Blocked - Audio File Not Found")

    def test_missing_backend_is_an_explicit_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "sample.mp3"
            audio.write_bytes(b"not an audio file")
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--input",
                    str(audio),
                    "--model",
                    __file__,
                    "--engine-bin",
                    "d13-missing-whisper-cli",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads(result.stdout)["status"], "Blocked - ASR Backend Not Available")

    def test_successful_transcription_leaves_no_text_next_to_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "sample.wav"
            model = root / "model.bin"
            engine = root / "fake-whisper-cli"
            model.write_bytes(b"model")
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=0.05",
                    str(audio),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            engine.write_text(
                "#!/bin/sh\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  if [ \"$1\" = \"-of\" ]; then\n"
                "    printf '候选人李四技术面试' > \"$2.txt\"\n"
                "    exit 0\n"
                "  fi\n"
                "  shift\n"
                "done\n"
                "exit 1\n",
                encoding="utf-8",
            )
            engine.chmod(0o755)
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--input",
                    str(audio),
                    "--model",
                    str(model),
                    "--engine-bin",
                    str(engine),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(payload["status"], "Transcribed")
            self.assertTrue(payload["temporary_artifacts_deleted"])
            self.assertFalse((root / "transcript.txt").exists())


if __name__ == "__main__":
    unittest.main()
