"""API tests for the /api/bots router."""

VALID_CODE = (
    "from remoteterm import bot\n"
    '@bot.on_keyword("hi")\n'
    "async def f(ctx, msg):\n"
    '    await ctx.reply("hey")\n'
)


class TestBotsCrud:
    async def test_create_list_update_delete(self, test_db, client):
        async with client:
            created = (
                await client.post("/api/bots", json={"name": "router-test", "code": VALID_CODE})
            ).json()
            assert created["name"] == "router-test"
            assert created["declared_keywords"] == ["hi"]

            listed = (await client.get("/api/bots")).json()
            assert any(b["id"] == created["id"] for b in listed)

            updated = (
                await client.patch(
                    f"/api/bots/{created['id']}",
                    json={"enabled": True, "ui_triggers": [{"kind": "keyword", "spec": "yo"}]},
                )
            ).json()
            assert updated["enabled"] is True
            assert updated["ui_triggers"] == [{"kind": "keyword", "spec": "yo"}]

            deleted = await client.delete(f"/api/bots/{created['id']}")
            assert deleted.status_code == 200
            assert (await client.get(f"/api/bots/{created['id']}")).status_code == 404

    async def test_create_rejects_bad_code(self, test_db, client):
        async with client:
            resp = await client.post(
                "/api/bots", json={"name": "bad-code", "code": "def broken(:\n"}
            )
            assert resp.status_code == 400
            assert "syntax error" in resp.json()["detail"]

    async def test_duplicate_name_conflict(self, test_db, client):
        async with client:
            first = await client.post("/api/bots", json={"name": "dupe", "code": VALID_CODE})
            assert first.status_code == 200
            second = await client.post("/api/bots", json={"name": "dupe", "code": VALID_CODE})
            assert second.status_code == 409

    async def test_bad_ui_cron_rejected(self, test_db, client):
        async with client:
            created = (
                await client.post("/api/bots", json={"name": "cron-check", "code": VALID_CODE})
            ).json()
            resp = await client.patch(
                f"/api/bots/{created['id']}",
                json={"ui_triggers": [{"kind": "cron", "spec": "99 * * * *"}]},
            )
            assert resp.status_code == 400

    async def test_create_from_library(self, test_db, client):
        async with client:
            library = (await client.get("/api/bots/library")).json()
            assert len(library) >= 30
            entry = next(e for e in library if e["key"] == "ping")
            created = (
                await client.post(
                    "/api/bots", json={"name": "my-ping", "from_builtin_key": entry["key"]}
                )
            ).json()
            assert created["declared_keywords"] == ["ping"]
            # A clone is a custom bot: no reset-to-builtin lineage.
            assert created["builtin_key"] is None


class TestFixedRoutesNotShadowed:
    async def test_feeds_test_route_is_not_a_bot_id(self, test_db, client):
        """POST /api/bots/feeds/test must hit the feed tester, not /{bot_id}/test."""
        async with client:
            resp = await client.post(
                "/api/bots/feeds/test",
                json={"url": "http://127.0.0.1/feed.xml", "feed_type": "rss"},
            )
            # The SSRF guard answers 400 — proving the feed route matched
            # (the bot test route would 404 on an unknown bot id or 422).
            assert resp.status_code == 400
            assert "private" in resp.json()["detail"]

    async def test_engine_and_stats_routes(self, test_db, client):
        async with client:
            engine = (await client.get("/api/bots/engine")).json()
            assert engine["settings"]["command_prefix"] == "!"
            stats = (await client.get("/api/bots/stats?window=24h")).json()
            assert "runs" in stats
            assert (await client.get("/api/bots/stats?window=bogus")).status_code == 400

    async def test_validate_cron_route(self, test_db, client):
        async with client:
            good = (await client.get("/api/bots/schedules/validate-cron?cron=%40daily")).json()
            assert good["valid"] and len(good["next_runs"]) == 3
            bad = (await client.get("/api/bots/schedules/validate-cron?cron=nope")).json()
            assert not bad["valid"]


class TestSchedulesAndFeeds:
    async def test_schedule_crud(self, test_db, client):
        async with client:
            created = (
                await client.post(
                    "/api/bots/schedules",
                    json={
                        "label": "morning",
                        "cron": "0 8 * * *",
                        "channel_key": "A" * 32,
                        "message": "gm",
                    },
                )
            ).json()
            assert created["next_run_at"] is not None
            listed = (await client.get("/api/bots/schedules/all")).json()
            assert any(s["id"] == created["id"] for s in listed)
            bad = await client.post(
                "/api/bots/schedules",
                json={"label": "x", "cron": "bad", "channel_key": "A" * 32, "message": "y"},
            )
            assert bad.status_code == 400
            assert (await client.delete(f"/api/bots/schedules/{created['id']}")).status_code == 200

    async def test_feed_crud_and_ssrf(self, test_db, client):
        async with client:
            bad = await client.post(
                "/api/bots/feeds",
                json={
                    "name": "local",
                    "feed_type": "rss",
                    "url": "http://127.0.0.1/x.xml",
                    "channel_key": "A" * 32,
                },
            )
            assert bad.status_code == 400
            created = (
                await client.post(
                    "/api/bots/feeds",
                    json={
                        "name": "blog",
                        "feed_type": "rss",
                        "url": "https://example.com/feed.xml",
                        "channel_key": "A" * 32,
                        "interval_seconds": 30,
                    },
                )
            ).json()
            assert created["interval_seconds"] == 60  # floored to 1 minute
            assert (await client.delete(f"/api/bots/feeds/{created['id']}")).status_code == 200


class TestEngineSettings:
    async def test_patch_validation(self, test_db, client):
        async with client:
            bad_mode = await client.patch("/api/bots/engine", json={"mention_mode": "sometimes"})
            assert bad_mode.status_code == 400
            bad_key = await client.patch(
                "/api/bots/engine",
                json={"admin_users": [{"public_key": "short", "name": "x"}]},
            )
            assert bad_key.status_code == 400
            ok = await client.patch(
                "/api/bots/engine",
                json={
                    "require_prefix": True,
                    "admin_users": [{"public_key": "AB" * 32, "name": "K0PHX"}],
                },
            )
            assert ok.status_code == 200
            body = ok.json()
            assert body["settings"]["require_prefix"] is True
            assert body["settings"]["admin_users"][0]["name"] == "K0PHX"


class TestInboundHooks:
    async def test_hook_requires_configured_token(self, test_db, client):
        from app.bots.engine import bot_engine
        from app.repository.bots import BotRepository

        code = (
            "from remoteterm import bot\n"
            '@bot.on_webhook("send")\n'
            "async def f(ctx, payload):\n"
            '    await ctx.send_dm(payload["dm_to"], payload["message"])\n'
            '@bot.on_webhook("sms")\n'
            "async def sms(ctx, payload):\n"
            '    ctx.state["incoming"] = payload\n'
        )
        bot = await BotRepository.create(name="hook-test", code=code, enabled=True)
        await bot_engine.reload_bot(bot.id)
        try:
            async with client:
                missing = await client.post("/api/hooks/send", json={"message": "x"})
                assert missing.status_code == 403  # token not configured

                await BotRepository.update(bot.id, settings={"webhook_token": "s3cret"})
                await bot_engine.reload_bot(bot.id)

                wrong = await client.post(
                    "/api/hooks/send", json={"message": "x"}, headers={"X-Hook-Token": "nope"}
                )
                assert wrong.status_code == 403

                unknown = await client.post("/api/hooks/nothere", json={})
                assert unknown.status_code == 404

                sms_get = await client.get(
                    "/api/hooks/sms",
                    params={"token": "s3cret", "from": "5145550100", "message": "hello"},
                )
                assert sms_get.status_code == 200
                assert sms_get.text == "ok"
                assert sms_get.headers["content-type"].startswith("text/plain")

                # GET support is deliberately limited to the VoIP.ms callback;
                # existing bot webhooks remain POST-only.
                other_get = await client.get("/api/hooks/send", params={"token": "s3cret"})
                assert other_get.status_code == 405
        finally:
            bot_engine.remove_bot(bot.id)


class TestKillSwitchUnified:
    async def test_engine_disable_also_disables_legacy_fanout_bots(self, test_db, client):
        """The Bots workspace kill switch must silence BOTH bot systems."""
        from app.bots.engine import bot_engine
        from app.fanout.manager import fanout_manager

        assert not fanout_manager.bots_disabled_effective()
        try:
            async with client:
                resp = await client.post("/api/bots/engine/disable-until-restart")
                assert resp.status_code == 200
            assert bot_engine.disabled_until_restart
            assert fanout_manager.bots_disabled_effective()
        finally:
            bot_engine.disabled_until_restart = False
            fanout_manager._bots_disabled_until_restart = False

    async def test_fanout_disable_also_disables_engine(self, test_db, client):
        from app.bots.engine import bot_engine
        from app.fanout.manager import fanout_manager

        try:
            async with client:
                resp = await client.post("/api/fanout/bots/disable-until-restart")
                assert resp.status_code == 200
            assert bot_engine.disabled_until_restart
            assert fanout_manager.bots_disabled_effective()
        finally:
            bot_engine.disabled_until_restart = False
            fanout_manager._bots_disabled_until_restart = False
