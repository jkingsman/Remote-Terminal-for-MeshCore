from app.bots.library import _merge_library_display_fields


def test_merge_library_display_fields_adds_only_missing_generated_urls():
    current = [{"key": "token", "type": "password"}]
    generated = {
        "key": "callback_url",
        "type": "generated_url",
        "template": "https://example.test/{token}/{EXTERNAL}",
    }
    shipped = [
        {"key": "token", "type": "text"},
        generated,
        {"key": "new_setting", "type": "text"},
    ]

    assert _merge_library_display_fields(current, shipped) == [*current, generated]


def test_merge_library_display_fields_preserves_operator_field_with_same_key():
    custom = {"key": "callback_url", "type": "url", "label": "My callback"}
    shipped = [{"key": "callback_url", "type": "generated_url", "template": "https://new"}]

    assert _merge_library_display_fields([custom], shipped) == [custom]
