from newsparser.bot.dispatcher import classify_message, MessageType


def test_cycle_command():
    assert classify_message("/cycle") == MessageType.SLASH_CYCLE


def test_morning_command():
    assert classify_message("/morning") == MessageType.SLASH_MORNING


def test_weekly_command():
    assert classify_message("/weekly") == MessageType.SLASH_WEEKLY


def test_reflect_command():
    assert classify_message("/reflect") == MessageType.SLASH_REFLECT


def test_free_text_query():
    assert classify_message("FOMC 결정이 삼성에 미치는 영향?") == MessageType.TRACKER_QUERY


def test_free_text_with_reference():
    assert classify_message("[2] 더 자세히 알려줘") == MessageType.TRACKER_QUERY


def test_empty_message():
    assert classify_message("") == MessageType.TRACKER_QUERY


def test_unknown_slash():
    assert classify_message("/unknown") == MessageType.TRACKER_QUERY
