from __future__ import annotations

import re
import statistics
import subprocess
import tempfile
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv"}
AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma",
    ".aiff", ".aif", ".ac3", ".mka", ".mp2", ".amr", ".m4b", ".oga",
    ".ape", ".caf",
}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".lrc", ".txt"}
LIBRARY_EXTENSIONS = MEDIA_EXTENSIONS | SUBTITLE_EXTENSIONS


@dataclass
class Caption:
    start_ms: int
    end_ms: int
    text: str
    speaker: str = ""
    translation: str = ""


@dataclass
class Attempt:
    index: int
    expected: str
    typed: str
    correct_words: int
    total_words: int
    word_results: list[tuple[str, str, bool]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def score(self) -> float:
        return 100.0 if self.total_words == 0 else self.correct_words / self.total_words * 100


def clean_raw_caption_parts(text: str) -> list[str]:
    # Bilingual ASS files commonly put Chinese and English on separate lines.
    # Prefer the Latin-script line when one exists, while keeping monolingual
    # Chinese/Japanese/Korean subtitles usable.
    parts = re.split(r"\\[Nn]|\r?\n", text)
    cleaned_parts: list[str] = []
    for part in parts:
        part = re.sub(r"<[^>]+>", "", part)
        part = re.sub(r"\{\\[^}]+}", "", part)
        part = re.sub(r"\s+", " ", part).strip()
        if part:
            cleaned_parts.append(part)
    return cleaned_parts


def clean_caption_parts(text: str) -> list[str]:
    cleaned_parts = clean_raw_caption_parts(text)
    latin_parts = [part for part in cleaned_parts if re.search(r"[A-Za-z]", part)]
    selected = latin_parts if latin_parts else cleaned_parts
    if len(selected) > 1 and all(re.match(r"^[-–—]\s*\S", part) for part in selected):
        return [re.sub(r"^[-–—]\s*", "", part) for part in selected]
    return [" ".join(selected)] if selected else []


def extract_translation(text: str) -> str:
    parts = clean_raw_caption_parts(text)
    translated = [part for part in parts if not re.search(r"[A-Za-z]", part) and re.search(r"[\u3400-\u9fff]", part)]
    return " ".join(translated)


def clean_caption_text(text: str) -> str:
    return " ".join(clean_caption_parts(text))


def extract_speaker(text: str, metadata_name: str = "") -> tuple[str, str]:
    ignored_names = {"", "ntp", "default", "*default", "unknown", "actor"}
    metadata = metadata_name.strip()
    speaker = metadata if metadata.casefold() not in ignored_names else ""
    match = re.match(r"^([A-Z][A-Za-z0-9 ._'’-]{1,24}):\s*(.+)$", text)
    if match:
        return match.group(1).strip().title(), match.group(2).strip()
    return speaker, text


def _finalize_captions(captions: list[Caption]) -> list[Caption]:
    captions = sorted(captions, key=lambda caption: (caption.start_ms, caption.end_ms))
    if not captions:
        return captions
    latin_count = sum(bool(re.search(r"[A-Za-z]", caption.text)) for caption in captions)
    # A mostly Latin subtitle track may contain a handful of Chinese-only title
    # cards. They are not useful for English dictation, so leave them out.
    if latin_count > 0 and latin_count >= len(captions) * 0.45:
        captions = [caption for caption in captions if re.search(r"[A-Za-z]", caption.text)]
    return captions


def _read_subtitle_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    for encoding in ("utf-8", "gb18030", "cp1252"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


def tokenize(text: str) -> list[str]:
    """Tokenize Latin words/numbers and CJK characters for live per-token checking."""
    return re.findall(
        r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+(?:['’\-][A-Za-zÀ-ÖØ-öø-ÿ0-9]+)*|"
        r"[\u3400-\u4dbf\u4e00-\u9fff]|"
        r"[\u3040-\u30ff]+|[\uac00-\ud7af]+",
        text,
    )


def _spelling_without_apostrophes(word: str) -> str:
    return (
        word.replace("’", "'")
        .replace("'", "")
        .strip(".,!?;:\"()[]{}…，。！？；：“”‘’")
    )


_SMALL_NUMBERS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17,
    "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
    "thirtieth": 30, "fortieth": 40, "fiftieth": 50, "sixtieth": 60,
    "seventieth": 70, "eightieth": 80, "ninetieth": 90,
}
_CHINESE_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _number_value(word: str) -> int | None:
    value = _spelling_without_apostrophes(word).casefold()
    numeric = re.fullmatch(r"([+-]?\d+)(?:st|nd|rd|th)?", value)
    if numeric:
        return int(numeric.group(1))
    if value in _SMALL_NUMBERS:
        return _SMALL_NUMBERS[value]
    if value in _TENS:
        return _TENS[value]
    if value in _ORDINALS:
        return _ORDINALS[value]
    if value in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[value]
    if "-" in value:
        first, second, *rest = value.split("-")
        if not rest and first in _TENS:
            if second in _SMALL_NUMBERS and 0 < _SMALL_NUMBERS[second] < 10:
                return _TENS[first] + _SMALL_NUMBERS[second]
            if second in _ORDINALS and 0 < _ORDINALS[second] < 10:
                return _TENS[first] + _ORDINALS[second]
    return None


def normalize_word(word: str) -> str:
    normalized = _spelling_without_apostrophes(word).casefold()
    number = _number_value(word)
    if number is not None:
        return f"#number:{number}"
    return "ok" if normalized in {"ok", "okay"} else normalized


def compare_words(
    expected: str,
    typed: str,
    proper_nouns: set[str] | None = None,
) -> tuple[int, list[tuple[str, str, bool]]]:
    expected_words = tokenize(expected)
    typed_words = tokenize(typed)
    known_proper = {normalize_word(word) for word in (proper_nouns or set())}
    sentence_initial: list[bool] = []
    cursor = 0
    for word in expected_words:
        start = expected.find(word, cursor)
        prefix = expected[:max(0, start)].rstrip()
        sentence_initial.append(not prefix or prefix[-1] in ".!?…")
        cursor = max(cursor, start + len(word))
    results: list[tuple[str, str, bool]] = []
    correct = 0
    for i, expected_word in enumerate(expected_words):
        typed_word = typed_words[i] if i < len(typed_words) else ""
        expected_normalized = normalize_word(expected_word)
        typed_normalized = normalize_word(typed_word)
        same_spelling = expected_normalized == typed_normalized
        preserve_case = (
            not expected_normalized.startswith("#number:")
            and
            expected_normalized not in {"ok", "i", "im", "ive", "ill", "id"}
            and (
                expected_normalized in known_proper
                or (not sentence_initial[i] and bool(expected_word) and expected_word[0].isupper())
                or (len(expected_word) > 1 and expected_word.isupper())
            )
        )
        same_case = _spelling_without_apostrophes(expected_word) == _spelling_without_apostrophes(typed_word)
        ok = same_spelling and (not preserve_case or same_case)
        correct += int(ok)
        results.append((expected_word, typed_word, ok))
    for extra in typed_words[len(expected_words):]:
        results.append(("", extra, False))
    return correct, results


def explain_spelling_difference(expected: str, typed: str) -> str:
    """Give a short character-level explanation for a wrong word."""
    if not typed:
        return "漏写"
    expected_clean = _spelling_without_apostrophes(expected)
    typed_clean = _spelling_without_apostrophes(typed)
    if expected_clean.casefold() == typed_clean.casefold() and expected_clean != typed_clean:
        return "大小写不符合专有名词写法"
    expected_lower = expected_clean.casefold()
    typed_lower = typed_clean.casefold()
    if len(expected_lower) == len(typed_lower):
        differences = [i for i, (left, right) in enumerate(zip(expected_lower, typed_lower)) if left != right]
        if (
            len(differences) == 2
            and differences[1] == differences[0] + 1
            and typed_lower[differences[0]] == expected_lower[differences[1]]
            and typed_lower[differences[1]] == expected_lower[differences[0]]
        ):
            start, end = differences[0], differences[1] + 1
            return f"字母顺序写反：{typed_clean[start:end]} 应为 {expected_clean[start:end]}"
    matcher = SequenceMatcher(None, expected_lower, typed_lower)
    changes = [opcode for opcode in matcher.get_opcodes() if opcode[0] != "equal"]
    if len(changes) == 1:
        tag, exp_start, exp_end, typed_start, typed_end = changes[0]
        expected_part = expected_clean[exp_start:exp_end]
        typed_part = typed_clean[typed_start:typed_end]
        if tag == "delete":
            return f"少了字母 {expected_part}"
        if tag == "insert":
            return f"多了字母 {typed_part}"
        if tag == "replace" and expected_part and typed_part:
            return f"字母 {typed_part} 应为 {expected_part}"
    return ""


def _parse_timestamp(value: str) -> int:
    value = value.strip().replace(".", ",")
    parts = value.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds_ms = parts
    else:
        hours, minutes, seconds_ms = parts
    seconds, milliseconds = (seconds_ms.split(",", 1) + ["0"])[:2]
    milliseconds = (milliseconds + "000")[:3]
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(milliseconds)


def parse_srt_like(content: str) -> list[Caption]:
    pattern = re.compile(
        r"(?:(?:^|\n)\s*\d+\s*\n)?"
        r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}|\d{1,2}:\d{2}[,.]\d{1,3})\s*-->\s*"
        r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}|\d{1,2}:\d{2}[,.]\d{1,3})[^\n]*\n"
        r"(?P<text>.*?)(?=\n\s*\n|\n\s*\d+\s*\n\d|\Z)",
        re.S | re.M,
    )
    captions: list[Caption] = []
    for match in pattern.finditer(content.replace("\r\n", "\n")):
        start_ms = _parse_timestamp(match.group("start"))
        end_ms = _parse_timestamp(match.group("end"))
        raw_text = match.group("text")
        translation = extract_translation(raw_text)
        parts = clean_caption_parts(raw_text)
        step = max(1, (end_ms - start_ms) // max(1, len(parts)))
        for index, part in enumerate(parts):
            speaker, text = extract_speaker(part)
            if text:
                part_start = start_ms + index * step
                part_end = end_ms if index == len(parts) - 1 else part_start + step
                captions.append(Caption(part_start, part_end, text, speaker, translation))
    return captions


def parse_lrc(content: str) -> list[Caption]:
    """Parse standard LRC timestamps, including bilingual lines at one time."""
    offset_match = re.search(r"(?im)^\[offset\s*:\s*([+-]?\d+)\s*\]", content)
    offset_ms = int(offset_match.group(1)) if offset_match else 0
    timestamp_pattern = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
    grouped: dict[int, list[str]] = {}
    for raw_line in content.splitlines():
        matches = list(timestamp_pattern.finditer(raw_line))
        if not matches:
            continue
        text = timestamp_pattern.sub("", raw_line).strip()
        if not text:
            continue
        for match in matches:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            fraction = match.group(3) or "0"
            milliseconds = int((fraction + "000")[:3]) if len(fraction) >= 3 else int(fraction) * (100 if len(fraction) == 1 else 10)
            start_ms = max(0, (minutes * 60 + seconds) * 1000 + milliseconds + offset_ms)
            grouped.setdefault(start_ms, []).append(text)
    starts = sorted(grouped)
    captions: list[Caption] = []
    for index, start_ms in enumerate(starts):
        raw_text = "\n".join(grouped[start_ms])
        translation = extract_translation(raw_text)
        parts = clean_caption_parts(raw_text)
        end_ms = starts[index + 1] if index + 1 < len(starts) else start_ms + 4000
        for part in parts:
            speaker, text = extract_speaker(part)
            if text:
                captions.append(Caption(start_ms, max(start_ms + 200, end_ms), text, speaker, translation))
    return captions


def parse_subtitle(path: str | Path) -> tuple[list[Caption], bool]:
    """Return captions and whether the source is untimed plain text."""
    subtitle_path = Path(path)
    content, detected_encoding = _read_subtitle_text(subtitle_path)

    if subtitle_path.suffix.lower() == ".lrc":
        lrc_captions = parse_lrc(content)
        if lrc_captions:
            return _finalize_captions(lrc_captions), False

    srt_captions = parse_srt_like(content)
    if srt_captions:
        return _finalize_captions(srt_captions), False

    if subtitle_path.suffix.lower() in {".ass", ".ssa", ".vtt"}:
        try:
            import pysubs2

            subs = pysubs2.load(str(subtitle_path), encoding=detected_encoding)
            captions: list[Caption] = []
            for line in subs:
                parts = clean_caption_parts(line.plaintext)
                step = max(1, (int(line.end) - int(line.start)) // max(1, len(parts)))
                for index, part in enumerate(parts):
                    speaker, text = extract_speaker(part, getattr(line, "name", ""))
                    if text:
                        part_start = int(line.start) + index * step
                        part_end = int(line.end) if index == len(parts) - 1 else part_start + step
                        captions.append(Caption(part_start, part_end, text, speaker, extract_translation(line.plaintext)))
            if captions:
                return _finalize_captions(captions), False
        except Exception:
            pass

    lines = [clean_caption_text(line) for line in content.splitlines()]
    lines = [line for line in lines if line and not line.startswith(("[Script Info]", "Format:", "Style:"))]
    captions = []
    for i, line in enumerate(lines):
        speaker, text = extract_speaker(line)
        captions.append(Caption(i * 4000, (i + 1) * 4000, text, speaker))
    return captions, True


def retime_plain_text(captions: Iterable[Caption], duration_ms: int) -> list[Caption]:
    items = list(captions)
    if not items or duration_ms <= 0:
        return items
    step = max(800, duration_ms // len(items))
    return [
        Caption(i * step, duration_ms if i == len(items) - 1 else (i + 1) * step, caption.text, caption.speaker, caption.translation)
        for i, caption in enumerate(items)
    ]


def synchronize_with_embedded(video_path: str | Path, external_captions: list[Caption]) -> tuple[list[Caption], int]:
    """Use embedded English timing/speaker boundaries and map external translations onto it."""
    embedded_path = extract_embedded_subtitle(video_path)
    if not embedded_path:
        return external_captions, 0
    embedded, _ = parse_subtitle(embedded_path)
    if not embedded:
        return external_captions, 0

    def comparable(text: str) -> str:
        return " ".join(token.casefold().replace("’", "'") for token in tokenize(text))

    synced: list[Caption] = []
    translated_count = 0
    for cue in embedded:
        target = comparable(cue.text)
        nearby = [
            index for index, external in enumerate(external_captions)
            if abs(external.start_ms - cue.start_ms) <= 12_000
        ]
        best_ratio = 0.0
        best_span: tuple[int, int] | None = None
        for start_index in nearby:
            for length in (1, 2, 3):
                end_index = start_index + length
                if end_index > len(external_captions):
                    continue
                span = external_captions[start_index:end_index]
                if span[-1].end_ms - span[0].start_ms > 12_000:
                    continue
                candidate = comparable(" ".join(item.text for item in span))
                ratio = SequenceMatcher(None, target, candidate).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_span = (start_index, end_index)
        translation = ""
        if best_span and best_ratio >= 0.48:
            values = [item.translation for item in external_captions[best_span[0]:best_span[1]] if item.translation]
            translation = " ".join(dict.fromkeys(values))
            translated_count += int(bool(translation))
        synced.append(Caption(cue.start_ms, cue.end_ms, cue.text, cue.speaker, translation))
    return synced, translated_count


def repair_external_only_captions(captions: list[Caption]) -> tuple[list[Caption], int]:
    """Conservatively merge obvious sentence fragments when no embedded track exists."""
    repaired: list[Caption] = []
    merged_count = 0
    index = 0
    while index < len(captions):
        current = captions[index]
        if index + 1 >= len(captions):
            repaired.append(current)
            break
        following = captions[index + 1]
        gap = following.start_ms - current.end_ms
        current_words = tokenize(current.text)
        following_words = tokenize(following.text)
        same_named_speaker = bool(current.speaker) and current.speaker == following.speaker
        both_unknown = not current.speaker and not following.speaker
        lowercase_continuation = bool(following.text and following.text[0].islower())
        current_is_unfinished = not re.search(r"[.!?…][\"'’”)]*\s*$", current.text)
        can_merge = (
            (same_named_speaker or (both_unknown and lowercase_continuation))
            and -100 <= gap <= 700
            and len(current_words) + len(following_words) <= 24
            and current_is_unfinished
        )
        if can_merge:
            translation = " ".join(value for value in (current.translation, following.translation) if value)
            repaired.append(
                Caption(
                    current.start_ms,
                    following.end_ms,
                    f"{current.text.rstrip()} {following.text.lstrip()}",
                    current.speaker or following.speaker,
                    translation,
                )
            )
            merged_count += 1
            index += 2
        else:
            repaired.append(current)
            index += 1
    return repaired, merged_count


def find_matching_subtitle(video_path: str | Path, search_root: str | Path | None = None) -> Path | None:
    video = Path(video_path)
    root = Path(search_root) if search_root else video.parent
    iterator = root.rglob("*") if root.exists() else ()
    candidates = [p for p in iterator if p.is_file() and p.suffix.lower() in SUBTITLE_EXTENSIONS]
    preference = lambda p: (p.parent != video.parent, p.suffix.lower() == ".txt", p.name.casefold())
    exact = [p for p in candidates if p.stem.casefold() == video.stem.casefold()]
    if exact:
        return sorted(exact, key=preference)[0]
    episode_match = re.search(r"(?i)(S\d{1,2}E\d{1,2})", video.stem)
    if episode_match:
        episode_key = episode_match.group(1).casefold()
        same_episode = [p for p in candidates if episode_key in p.stem.casefold()]
        if same_episode:
            return sorted(same_episode, key=preference)[0]
    starts = [p for p in candidates if p.stem.casefold().startswith(video.stem.casefold())]
    if starts:
        return sorted(starts, key=lambda p: p.name)[0]
    # A completely unfamiliar file name is still unambiguous when the folder
    # contains only one usable subtitle file.
    return candidates[0] if len(candidates) == 1 else None


def list_library_files(folder: str | Path) -> list[Path]:
    root = Path(folder)
    return sorted(
        (p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in LIBRARY_EXTENSIONS),
        key=lambda p: (str(p.relative_to(root).parent).casefold(), p.name.casefold()),
    )


def list_media_files(folder: str | Path) -> list[Path]:
    return [path for path in list_library_files(folder) if path.suffix.lower() in MEDIA_EXTENSIONS]


def list_videos(folder: str | Path) -> list[Path]:
    """Backward-compatible name retained for older callers."""
    return list_media_files(folder)


def bundled_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _embedded_cache_path(video_path: str | Path, suffix: str = "embedded") -> Path:
    output_dir = Path(tempfile.gettempdir()) / "shadowing_trainer"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{Path(video_path).stem}_{suffix}.srt"


def extract_embedded_subtitle(video_path: str | Path) -> Path | None:
    """Extract the first embedded text subtitle stream as SRT, if present."""
    ffmpeg = bundled_ffmpeg()
    if not ffmpeg:
        return None
    output = _embedded_cache_path(video_path)
    if output.exists() and output.stat().st_size and output.stat().st_mtime >= Path(video_path).stat().st_mtime:
        return output
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-map",
        "0:s:0",
        "-c:s",
        "srt",
        str(output),
    ]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(command, capture_output=True, creationflags=creation_flags, timeout=90)
    return output if result.returncode == 0 and output.exists() and output.stat().st_size else None


def detect_subtitle_offset(video_path: str | Path, external_captions: list[Caption]) -> int | None:
    """Compare early external cues with embedded text cues and return offset ms."""
    full_cache = _embedded_cache_path(video_path)
    sample_path = full_cache if full_cache.exists() and full_cache.stat().st_size else _embedded_cache_path(video_path, "alignment")
    if sample_path is not full_cache and not (sample_path.exists() and sample_path.stat().st_size):
        ffmpeg = bundled_ffmpeg()
        if not ffmpeg:
            return None
        command = [
            ffmpeg, "-y", "-i", str(video_path), "-t", "180",
            "-map", "0:s:0", "-c:s", "srt", str(sample_path),
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(command, capture_output=True, creationflags=creation_flags, timeout=45)
        except (subprocess.SubprocessError, OSError):
            return None
        if result.returncode != 0 or not sample_path.exists() or not sample_path.stat().st_size:
            return None
    try:
        embedded, _ = parse_subtitle(sample_path)
    except Exception:
        return None

    def comparable(text: str) -> str:
        return "".join(ch for ch in text.casefold() if ch.isalnum())

    embedded_early = [caption for caption in embedded if caption.start_ms <= 180_000]
    offsets: list[int] = []
    for external in (caption for caption in external_captions if caption.start_ms <= 180_000):
        source = comparable(external.text)
        if len(source) < 5:
            continue
        candidates = [caption for caption in embedded_early if abs(caption.start_ms - external.start_ms) <= 8_000]
        best_ratio = 0.0
        best_caption: Caption | None = None
        for candidate in candidates:
            ratio = SequenceMatcher(None, source, comparable(candidate.text)).ratio()
            if ratio > best_ratio:
                best_ratio, best_caption = ratio, candidate
        if best_caption and best_ratio >= 0.62:
            offsets.append(best_caption.start_ms - external.start_ms)
    if len(offsets) < 3:
        return None
    median = statistics.median(offsets)
    consistent = [offset for offset in offsets if abs(offset - median) <= 500]
    if len(consistent) < 3:
        return None
    return int(round(statistics.median(consistent) / 50.0) * 50)


def format_ms(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def export_txt(path: str | Path, media_name: str, attempts: list[Attempt]) -> None:
    answered = len(attempts)
    total_words = sum(a.total_words for a in attempts)
    correct_words = sum(a.correct_words for a in attempts)
    score = 100.0 if total_words == 0 else correct_words / total_words * 100
    lines = [
        "Shadowing 拼写练习报告",
        f"媒体：{media_name}",
        f"导出时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"已练句数：{answered}",
        f"单词正确率：{correct_words}/{total_words}（{score:.1f}%）",
        "",
    ]
    for attempt in attempts:
        wrong = [f"{typed or '（漏）'} → {expected or '（多余）'}" for expected, typed, ok in attempt.word_results if not ok]
        lines.extend(
            [
                f"第 {attempt.index + 1} 句｜{attempt.score:.1f}%｜{attempt.created_at:%H:%M:%S}",
                f"原句：{attempt.expected}",
                f"输入：{attempt.typed or '（未输入）'}",
                f"错词：{'；'.join(wrong) if wrong else '无'}",
                "",
            ]
        )
    Path(path).write_text("\n".join(lines), encoding="utf-8-sig")


def export_docx(path: str | Path, media_name: str, attempts: list[Attempt]) -> None:
    from docx import Document
    from docx.shared import Pt

    document = Document()
    styles = document.styles
    styles["Normal"].font.name = "Microsoft YaHei"
    styles["Normal"].font.size = Pt(10.5)
    document.add_heading("Shadowing 拼写练习报告", 0)
    total_words = sum(a.total_words for a in attempts)
    correct_words = sum(a.correct_words for a in attempts)
    score = 100.0 if total_words == 0 else correct_words / total_words * 100
    document.add_paragraph(f"媒体：{media_name}")
    document.add_paragraph(f"导出时间：{datetime.now():%Y-%m-%d %H:%M}")
    document.add_paragraph(f"已练句数：{len(attempts)}　单词正确率：{correct_words}/{total_words}（{score:.1f}%）")

    table = document.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    headers = ["句号", "得分", "原句", "我的输入", "错词（输入 → 正确）"]
    for cell, header in zip(table.rows[0].cells, headers):
        cell.text = header
    for attempt in attempts:
        cells = table.add_row().cells
        wrong = [f"{typed or '漏词'} → {expected or '多余词'}" for expected, typed, ok in attempt.word_results if not ok]
        values = [
            str(attempt.index + 1),
            f"{attempt.score:.1f}%",
            attempt.expected,
            attempt.typed or "（未输入）",
            "；".join(wrong) if wrong else "无",
        ]
        for cell, value in zip(cells, values):
            cell.text = value
    document.save(str(path))
