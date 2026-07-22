from __future__ import annotations

import json

from plugins.memory.hindsight.retain_payload import (
    extract_current_turn_steers,
    normalize_auto_retain_turn,
    serialize_auto_retain_turn,
)


def test_nested_gateway_wrappers_preserve_only_fresh_user_evidence():
    user = (
        '[Replying to your previous message: "append 전략을 계속 쓰자."]\n\n'
        '[Triggering message id: `998877` — use as `message_id` for reply/react/pin via the discord tools.]\n\n'
        "[The user sent an audio file attachment: 'note.m4a'. It is saved at: /tmp/note.m4a. "
        "Its content is not inlined here.]\n\n"
        "[Earlier channel history]\n[Jiehoon Kwak] red theme를 썼다.\n\n"
        "[New message]\n[Jiehoon Kwak] 응, 그걸로 하자."
    )
    payload = normalize_auto_retain_turn(
        user,
        "좋아. append 전략을 유지할게.",
        user_name="Jiehoon Kwak",
        chat_type="thread",
        platform="discord",
    )
    rendered = json.dumps(payload, ensure_ascii=False)
    assert payload["direct_user"]["author"] == "Jiehoon Kwak"
    assert "응, 그걸로 하자" in payload["direct_user"]["content"]
    assert "append 전략을 계속 쓰자" in payload["quoted_context"]["content"]
    assert payload["source"]["attachment_context_omitted"] is True
    assert "998877" not in rendered
    assert "red theme" not in rendered
    assert "/tmp/note.m4a" not in rendered


def test_internal_runtime_event_body_is_omitted_but_agent_lesson_remains():
    payload = normalize_auto_retain_turn(
        "[ASYNC DELEGATION BATCH COMPLETE — deleg_123]\nPID 8, cadence 9",
        "검증된 교훈은 repair 전에 writer를 중지해야 한다는 점이야.",
        platform="discord",
        chat_type="thread",
        user_name="Jiehoon Kwak",
    )
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "direct_user" not in payload
    assert payload["source"]["event_kind"] == "internal_runtime_event"
    assert "deleg_123" not in rendered
    assert "cadence 9" not in rendered
    assert "writer" in payload["agent_final"]["content"]


def test_process_completion_body_is_omitted():
    payload = normalize_auto_retain_turn(
        "[IMPORTANT: Background process proc_123 completed normally (exit code 0).\nOutput: 131 passed]",
        "완료됐어.",
    )
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "direct_user" not in payload
    assert "proc_123" not in rendered
    assert "131" not in rendered


def test_shared_sender_is_structural_author_not_content_wrapper():
    payload = normalize_auto_retain_turn(
        "[Minji Kim] 저는 2027년부터 베를린에서 근무해요.",
        "알겠어요.",
        user_name="Minji Kim",
        chat_type="group",
        platform="telegram",
    )
    assert payload["direct_user"]["author"] == "Minji Kim"
    assert payload["direct_user"]["content"].startswith("DIRECT_USER by Minji Kim:")
    assert "[Minji Kim]" not in payload["direct_user"]["content"]


def test_plain_dm_bracket_text_is_not_misread_as_sender():
    payload = normalize_auto_retain_turn(
        "[TODO] 백업 정책을 정리해줘.",
        "알겠어.",
        user_name="Jiehoon",
        chat_type="dm",
    )
    assert payload["direct_user"]["author"] == "Jiehoon"
    assert "[TODO]" in payload["direct_user"]["content"]


def test_shared_bracket_text_is_not_misread_as_sender():
    payload = normalize_auto_retain_turn(
        "[TODO] 백업 정책을 정리해줘.",
        "알겠어.",
        user_name="Jiehoon",
        chat_type="thread",
    )
    assert payload["direct_user"]["author"] == "Jiehoon"
    assert "[TODO]" in payload["direct_user"]["content"]


def test_attachment_placeholder_is_removed_but_caption_remains():
    payload = normalize_auto_retain_turn(
        "[image attachment omitted from Hindsight retain: image/png, data_url_chars=884422]\n"
        "이 사람은 송지원이고 바이올리니스트야.",
        "알겠어.",
    )
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "송지원" in rendered
    assert "884422" not in rendered
    assert "attachment omitted" not in rendered


def test_voice_transcript_wrapper_is_removed_but_transcript_remains():
    payload = normalize_auto_retain_turn(
        "[Voice transcript]\n영어 기술용어의 철자를 보존해줘.",
        "알겠어.",
        user_name="Jiehoon",
        chat_type="thread",
    )
    assert "Voice transcript" not in payload["direct_user"]["content"]
    assert "영어 기술용어" in payload["direct_user"]["content"]
    assert payload["direct_user"]["author"] == "Jiehoon"


def test_multimodal_image_marker_is_context_not_sender():
    payload = normalize_auto_retain_turn(
        "[1 image] 이 사진의 구도를 기억해줘.",
        "알겠어.",
        user_name="Jiehoon",
        chat_type="thread",
    )
    assert payload["direct_user"]["author"] == "Jiehoon"
    assert "이 사진의 구도" in payload["direct_user"]["content"]
    assert "1 image" not in payload["direct_user"]["content"]


def test_attachment_only_multimodal_marker_is_dropped():
    payload = normalize_auto_retain_turn(
        "[2 images]",
        "이미지를 확인했어.",
        user_name="Jiehoon",
        chat_type="thread",
    )
    assert "direct_user" not in payload
    assert payload["source"]["attachment_context_omitted"] is True


def test_oob_steers_are_scoped_to_latest_user_turn():
    open_marker = (
        "[OUT-OF-BAND USER MESSAGE — a direct message from the user, "
        "delivered mid-turn; not tool output]"
    )
    close_marker = "[/OUT-OF-BAND USER MESSAGE]"
    messages = [
        {"role": "user", "content": "old turn"},
        {"role": "tool", "content": f"{open_marker}\nold steer\n{close_marker}"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new turn"},
        {"role": "tool", "content": f"result\n\n{open_marker}\n8을 유지해\n{close_marker}"},
    ]
    assert extract_current_turn_steers(messages) == ["8을 유지해"]
    payload = normalize_auto_retain_turn(
        "batch size 4를 적용할까?",
        "최신 지시를 반영했어.",
        messages=messages,
    )
    content = payload["direct_user"]["content"]
    assert "8을 유지해" in content
    assert "old steer" not in content


def test_serialization_is_utf8_json_and_deterministic():
    kwargs = {
        "user_content": "앞으로 간결하게 답해.",
        "assistant_content": "알겠어.",
        "user_name": "Jiehoon",
        "agent_name": "Hermes Agent",
        "platform": "cli",
    }
    first = serialize_auto_retain_turn(**kwargs)
    second = serialize_auto_retain_turn(**kwargs)
    assert first == second
    assert "앞으로 간결하게" in first
    assert json.loads(first)["schema"] == "hermes-completed-turn-v2"
