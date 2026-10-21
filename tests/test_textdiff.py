from speak.textdiff import count_words, diff_words, repeat_score, tokenize


def test_tokenize_expands_contractions_but_keeps_original_spelling():
    tokens = tokenize("I'm fine")
    assert [t.norm for t in tokens] == ["i", "am", "fine"]
    assert [t.text for t in tokens] == ["I'm", "", "fine"]


def test_diff_marks_replaced_and_inserted_words():
    diff = diff_words("I go to cinema yesterday", "I went to the cinema yesterday.")
    assert diff == [
        {"text": "I", "op": "equal"},
        {"text": "go", "op": "delete"},
        {"text": "went", "op": "insert"},
        {"text": "to", "op": "equal"},
        {"text": "the", "op": "insert"},
        {"text": "cinema", "op": "equal"},
        {"text": "yesterday", "op": "equal"},
    ]


def test_diff_ignores_case_punctuation_and_contraction_form():
    diff = diff_words("i am happy", "I'm happy!")
    assert all(part["op"] == "equal" for part in diff)
    assert [p["text"] for p in diff] == ["I'm", "happy"]


def test_repeat_score_counts_words_said_in_order():
    result = repeat_score("I went to the cinema", "I went to cinema")
    assert result["score"] == 80
    assert [w["text"] for w in result["words"] if not w["ok"]] == ["the"]


def test_repeat_score_of_empty_target_is_zero():
    assert repeat_score("", "anything") == {"score": 0, "words": []}


def test_count_words():
    assert count_words("Well, I don't know... maybe!") == 5
    assert count_words("") == 0
