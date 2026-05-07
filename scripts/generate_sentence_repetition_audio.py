"""
Generates wav files for the Sentence Repetition template.

Three priming-presentation conditions + two fillers, all in English,
synthesised with one voice:

  type1 — prime is a separate sentence preceding the target.
  type2 — prime is the second clause of the same sentence as the target;
          by the time the participant produces the target, the prime
          has been perceived but not yet produced.
  type3 — clause order is varied (prime-first vs target-first), so the
          prime can either precede or follow the target. Both orderings
          are emitted as 03a / 03b.

Run:
    python -m scripts.generate_sentence_repetition_audio
"""

import asyncio
import io
import shutil
import sys
import tempfile
from pathlib import Path

import edge_tts
from pydub import AudioSegment

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


VOICE = "en-US-AriaNeural"
RATE = "-5%"
PAUSE_MS = 700  # gap between two separate sentences in type1

OUT_DIR = Path(__file__).resolve().parent.parent / "templates" / "examples" / "media"

# Both prime and target share PP-dative structure ("V NP to NP"),
# which is the construction the prime is intended to reinforce.
PRIME = "The teacher handed a book to the student."
TARGET = "The waiter brought a menu to the guest."

TRIALS: list[tuple[str, list[str]]] = [
    # type1: two separate sentences, prime first.
    ("type1_separate_01.wav", [PRIME, TARGET]),

    # type2: one sentence, target as the first clause, prime as the
    # second clause. Surface order: target, then prime.
    (
        "type2_prime_second_clause_02.wav",
        [f"{TARGET[:-1]}, and {PRIME[0].lower()}{PRIME[1:]}"],
    ),

    # type3a: one sentence, prime as the first clause, target as the
    # second clause.
    (
        "type3a_prime_first_03.wav",
        [f"{PRIME[:-1]}, and {TARGET[0].lower()}{TARGET[1:]}"],
    ),

    # type3b: one sentence, target as the first clause, prime as the
    # second clause (same surface form as type2 — the difference is
    # experimental role, not acoustics; type3 manipulates order as a
    # variable, type2 fixes prime-after-target).
    (
        "type3b_target_first_04.wav",
        [f"{TARGET[:-1]}, and {PRIME[0].lower()}{PRIME[1:]}"],
    ),

    ("filler_01.wav", ["The garden is full of beautiful red roses."]),
    ("filler_02.wav", ["A hot cup of tea is sitting on the wooden table."]),
]


async def synth_to_mp3(text: str, mp3_path: Path) -> None:
    communicate = edge_tts.Communicate(text, VOICE, rate=RATE)
    await communicate.save(str(mp3_path))


async def build_trial(filename: str, sentences: list[str], tmp_dir: Path) -> Path:
    parts: list[AudioSegment] = []
    silence = AudioSegment.silent(duration=PAUSE_MS)

    for idx, sentence in enumerate(sentences):
        mp3_path = tmp_dir / f"{filename}_{idx}.mp3"
        await synth_to_mp3(sentence, mp3_path)
        seg = AudioSegment.from_file(mp3_path, format="mp3")
        if idx > 0:
            parts.append(silence)
        parts.append(seg)

    combined = sum(parts[1:], parts[0])
    out_path = OUT_DIR / filename
    combined = combined.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    combined.export(out_path, format="wav")
    return out_path


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for filename, sentences in TRIALS:
            out_path = await build_trial(filename, sentences, tmp_dir)
            print(f"  [ok] {out_path.relative_to(OUT_DIR.parent.parent)}")


if __name__ == "__main__":
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg not found in PATH — pydub cannot convert mp3 to wav")
    asyncio.run(main())
