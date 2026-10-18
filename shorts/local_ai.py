"""Isolated CPU inference. Models leave RAM when this subprocess exits."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main():
    request = json.loads(Path(sys.argv[2]).read_text("utf-8"))
    os.environ["HF_HOME"] = request["cache"]
    os.environ["OMP_NUM_THREADS"] = "4"
    if not request["downloads"]:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    out = Path(request["output"])
    if sys.argv[1] == "speech":
        from vieneu import Vieneu
        engine = Vieneu(mode="v3turbo", backend="onnx", precision="fp32")
        out.mkdir(exist_ok=True, parents=True)
        for index, text in enumerate(request["texts"]):
            audio = engine.infer(text, voice=request["voice"])
            engine.save(audio, str(out / f"{index:03d}.wav"))
    elif sys.argv[1] == "captions":
        from faster_whisper import WhisperModel
        model = WhisperModel(request["model"], device="cpu", compute_type="int8",
                             cpu_threads=4, num_workers=1, download_root=request["cache"],
                             local_files_only=not request["downloads"])
        segments, _ = model.transcribe(request["audio"], language="vi", word_timestamps=True,
                                       initial_prompt=request["prompt"], vad_filter=True)
        words = [dict(text=w.word.strip(), start=w.start, end=w.end)
                 for segment in segments for w in (segment.words or []) if w.word.strip()]
        out.write_text(json.dumps(words, ensure_ascii=False), "utf-8")
    else:
        raise ValueError("Unknown inference operation")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Detailed trace is local; worker turns missing models into actionable needs_input.
        print(f"LOCAL_AI_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
