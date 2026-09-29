from pathlib import Path

from core import AnnotationNote, Attempt, Caption, HighlightNote, compare_words, explain_spelling_difference, export_docx, export_txt, find_matching_subtitle, list_library_files, list_media_files, parse_subtitle, repair_external_only_captions, tokenize


def test_tokenize_and_compare():
    assert tokenize("I'm learning English, today!") == ["I'm", "learning", "English", "today"]
    correct, results = compare_words("I'm learning English today.", "i'm learning Englis today")
    assert correct == 3
    assert results[2] == ("English", "Englis", False)


def test_apostrophes_are_optional_when_typing():
    correct, results = compare_words("He's sure I don't mind Jimmy's car.", "hes sure I dont mind Jimmys car")
    assert correct == 7
    assert all(ok for _expected, _typed, ok in results)


def test_ok_and_okay_are_equivalent_and_normal_case_is_ignored():
    correct, results = compare_words("Okay, I'm ready now.", "OK im READY NOW")
    assert correct == 4
    assert all(ok for _expected, _typed, ok in results)


def test_only_proper_nouns_require_matching_case():
    correct, results = compare_words("Call Jimmy now.", "call jimmy NOW")
    assert correct == 2
    assert results[0] == ("Call", "call", True)
    assert results[1] == ("Jimmy", "jimmy", False)
    assert results[2] == ("now", "NOW", True)
    correct, _ = compare_words("Jimmy is here.", "jimmy IS HERE", {"Jimmy"})
    assert correct == 2
    correct, results = compare_words("Hello. Good to see Kim.", "hello. good TO SEE kim")
    assert correct == 4
    assert results[1] == ("Good", "good", True)
    assert results[4] == ("Kim", "kim", False)


def test_digits_english_numbers_and_chinese_digits_are_equivalent():
    correct, results = compare_words("I need three apples and twenty-three oranges.", "i NEED 3 APPLES AND 23 ORANGES")
    assert correct == 7
    assert all(ok for _expected, _typed, ok in results)
    correct, _ = compare_words("Choose three on the first try.", "choose 三 ON THE 1st TRY")
    assert correct == 6


def test_established_regression_and_transposition_explanation():
    correct, results = compare_words("The woods are well established.", "the woods are WELL established")
    assert correct == 5
    assert all(ok for _expected, _typed, ok in results)
    correct, results = compare_words("well established", "well estabilshed")
    assert correct == 1
    assert results[1] == ("established", "estabilshed", False)
    assert explain_spelling_difference("established", "estabilshed") == "字母顺序写反：il 应为 li"


def test_srt_parse(tmp_path: Path):
    subtitle = tmp_path / "sample.srt"
    subtitle.write_text(
        "1\n00:00:01,000 --> 00:00:03,500\nHello world!\n\n2\n00:00:04,000 --> 00:00:05,000\nAgain.\n",
        encoding="utf-8",
    )
    captions, plain = parse_subtitle(subtitle)
    assert not plain
    assert len(captions) == 2
    assert captions[0].start_ms == 1000
    assert captions[0].text == "Hello world!"


def test_bilingual_srt_keeps_chinese_answer(tmp_path: Path):
    subtitle = tmp_path / "bilingual.srt"
    subtitle.write_text(
        "1\n00:00:01,000 --> 00:00:03,500\n你好，吉米\nHello, Jimmy.\n",
        encoding="utf-8",
    )
    captions, plain = parse_subtitle(subtitle)
    assert not plain
    assert captions[0].text == "Hello, Jimmy."
    assert captions[0].translation == "你好，吉米"


def test_plain_txt(tmp_path: Path):
    transcript = tmp_path / "sample.txt"
    transcript.write_text("First sentence.\nSecond sentence.\n", encoding="utf-8")
    captions, plain = parse_subtitle(transcript)
    assert plain
    assert [c.text for c in captions] == ["First sentence.", "Second sentence."]


def test_episode_matching_and_bilingual_ass(tmp_path: Path):
    video = tmp_path / "Show.S02E01.Title.1080p.BluRay.mkv"
    subtitle = tmp_path / "Show.S02E01.Title.1080p.WEB-DL.ChsEngA.ass"
    video.touch()
    subtitle.write_text(
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:02.00,0:00:03.00,Default,,0,0,0,,\u4f60\u597d\\N{\\fs12}Hello there.\n"
        "Dialogue: 0,0:00:00.50,0:00:01.00,Default,,0,0,0,,\u4e2d\u6587\u6807\u9898\n",
        encoding="utf-8",
    )
    assert find_matching_subtitle(video) == subtitle
    captions, plain = parse_subtitle(subtitle)
    assert not plain
    assert [caption.text for caption in captions] == ["Hello there."]
    assert captions[0].translation == "你好"


def test_two_speakers_are_split_and_named(tmp_path: Path):
    subtitle = tmp_path / "dialogue.srt"
    subtitle.write_text(
        "1\n00:00:01,000 --> 00:00:05,000\n- JIMMY: Are you okay?\n- KIM: I'm fine.\n",
        encoding="utf-8",
    )
    captions, plain = parse_subtitle(subtitle)
    assert not plain
    assert [(caption.speaker, caption.text) for caption in captions] == [
        ("Jimmy", "Are you okay?"),
        ("Kim", "I'm fine."),
    ]
    assert [(caption.start_ms, caption.end_ms) for caption in captions] == [(1000, 3000), (3000, 5000)]


def test_external_only_repair_is_conservative():
    captions = [
        Caption(0, 900, "I was thinking", ""),
        Caption(950, 1700, "about going home.", ""),
        Caption(2000, 2700, "Hello,", ""),
        Caption(2750, 3400, "Jimmy, right on time.", ""),
    ]
    repaired, merged_count = repair_external_only_captions(captions)
    assert merged_count == 1
    assert repaired[0].text == "I was thinking about going home."
    assert repaired[1].text == "Hello,"
    assert repaired[2].text == "Jimmy, right on time."


def test_single_unrelated_subtitle_is_still_detected(tmp_path: Path):
    video = tmp_path / "completely-unknown-video.mkv"
    subtitle = tmp_path / "my-transcript.srt"
    video.touch()
    subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello.\n", encoding="utf-8")
    assert find_matching_subtitle(video) == subtitle


def test_folder_scanner_includes_common_audio(tmp_path: Path):
    expected = [tmp_path / "lesson.flac", tmp_path / "lesson.mp3", tmp_path / "lesson.opus", tmp_path / "lesson.wav"]
    for path in expected:
        path.touch()
    (tmp_path / "notes.pdf").touch()
    assert list_media_files(tmp_path) == expected


def test_lrc_timing_translation_and_offset(tmp_path: Path):
    lyrics = tmp_path / "lesson.lrc"
    lyrics.write_text(
        "[ar:Teacher]\n[offset:100]\n[00:01.00]你好\n[00:01.00]Hello.\n[00:03.50]Next line.\n",
        encoding="utf-8",
    )
    captions, plain = parse_subtitle(lyrics)
    assert not plain
    assert [(c.start_ms, c.end_ms, c.text) for c in captions] == [
        (1100, 3600, "Hello."),
        (3600, 7600, "Next line."),
    ]
    assert captions[0].translation == "你好"


def test_recursive_library_and_subfolder_subtitle_matching(tmp_path: Path):
    media = tmp_path / "Season 1" / "Show.S01E02.mp3"
    subtitle = tmp_path / "Subs" / "Show.S01E02.en.lrc"
    media.parent.mkdir()
    subtitle.parent.mkdir()
    media.touch()
    subtitle.write_text("[00:00.00]Hello.\n", encoding="utf-8")
    assert list_library_files(tmp_path) == [media, subtitle]
    assert find_matching_subtitle(media, tmp_path) == subtitle


def test_highlight_notes_are_exported_to_txt_and_word(tmp_path: Path):
    attempts = [Attempt(0, "The woods are well established.", "The woods are well established", 5, 5)]
    highlights = [
        HighlightNote(
            index=0,
            sentence="The woods are well established.",
            selected="well established",
            selection_start=14,
            selection_end=30,
            speaker="Neil",
            start_ms=12340,
        )
    ]
    annotations = [
        AnnotationNote(
            index=0,
            sentence="The woods are well established.",
            quote="established",
            note="常用搭配：well established",
            selection_start=19,
            selection_end=30,
            speaker="Neil",
            start_ms=12340,
        )
    ]
    txt_path = tmp_path / "notes.txt"
    docx_path = tmp_path / "notes.docx"
    export_txt(txt_path, "lesson.mp4", attempts, highlights, annotations)
    exported = txt_path.read_text(encoding="utf-8-sig")
    assert "荧光摘录（1 处）" in exported
    assert "标记：well established" in exported
    assert "第 1 句｜00:12｜Neil" in exported
    assert "批注原文：established" in exported
    assert "文字笔记：常用搭配：well established" in exported
    export_docx(docx_path, "lesson.mp4", attempts, highlights, annotations)
    assert docx_path.stat().st_size > 0
