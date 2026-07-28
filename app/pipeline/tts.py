"""Voix off via edge-tts, avec horodatage exact de chaque mot (WordBoundary)."""
import asyncio
from pathlib import Path

import edge_tts


async def _synth(text: str, voice: str, rate: str, mp3_path: Path) -> list[dict]:
    # boundary="WordBoundary" obligatoire depuis edge-tts 7.x (défaut = phrase)
    com = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    words: list[dict] = []
    with open(mp3_path, "wb") as f:
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # offset/duration en unités de 100 ns
                start = chunk["offset"] / 1e7
                words.append({
                    "word": chunk["text"],
                    "start": start,
                    "end": start + chunk["duration"] / 1e7,
                })
    if not words:
        raise RuntimeError("edge-tts n'a renvoyé aucun WordBoundary")
    return words


def synthesize(text: str, voice: str, rate: str, mp3_path: Path) -> list[dict]:
    """Écrit le mp3 et retourne [{word, start, end}, ...]."""
    return asyncio.run(_synth(text, voice, rate, mp3_path))
