async def test_seeding_preserves_operator_modified_bot_schema(test_db):
    from app.bots.library import ensure_seeded, get_library_entry
    from app.repository.bots import BotRepository

    entry = get_library_entry("sms")
    assert entry is not None
    custom_schema = [{"key": "operator_field", "type": "text"}]
    record = await BotRepository.create(
        name="SMS customized",
        code=entry["code"],
        settings_schema=custom_schema,
        builtin_key="sms",
        builtin_version="0.0.1",
        modified=True,
    )

    await ensure_seeded()

    refreshed = await BotRepository.get(record.id)
    assert refreshed is not None
    assert refreshed.settings_schema == custom_schema
    assert refreshed.builtin_version == "0.0.1"
