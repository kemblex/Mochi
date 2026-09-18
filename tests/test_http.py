"""Run with: .venv/bin/python -m unittest discover -s tests -v"""
import importlib
import sqlite3
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


# Import without logging into Discord, loading secrets, or opening the real database.
database = sqlite3.connect(":memory:")
with patch("sqlite3.connect", return_value=database), patch("dotenv.load_dotenv"):
    bot = importlib.import_module("bot")


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.execute("DELETE FROM users")
        database.commit()
        bot.pairing_codes.clear()
        self.channel = MagicMock(spec=discord.abc.Messageable)
        self.channel.send = AsyncMock()
        self.discord = MagicMock()
        self.discord.is_ready.return_value = True
        self.discord.get_channel.return_value = self.channel
        self.discord.fetch_channel = AsyncMock(return_value=self.channel)
        self.client_patch = patch.object(bot, "client", self.discord)
        self.client_patch.start()
        app = web.Application()
        app.router.add_post("/pair", bot.receive_pair)
        app.router.add_post("/event", bot.receive_event)
        self.http = TestClient(TestServer(app))
        await self.http.start_server()

    async def asyncTearDown(self):
        await self.http.close()
        self.client_patch.stop()

    def set_channel(self, user=123, channel=456):
        database.execute("INSERT OR REPLACE INTO users VALUES (?, ?)", (user, channel))
        database.commit()

    async def event(self, **changes):
        data = {"type": "chat_text", "user_id": 123, "text": "hello"}
        data.update(changes)
        return await self.http.post("/event", json=data)

    async def test_pair_command_and_single_use_exchange(self):
        interaction = SimpleNamespace(user=SimpleNamespace(id=123), response=SimpleNamespace(send_message=AsyncMock()))
        await bot.pair.callback(interaction)
        code = next(iter(bot.pairing_codes))
        self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])
        response = await self.http.post("/pair", json={"code": code})
        self.assertEqual(await response.json(), {"ok": True, "type": "pair_success", "user_id": 123})
        response = await self.http.post("/pair", json={"code": code})
        self.assertEqual(response.status, 400)

    async def test_expired_pairing(self):
        bot.pairing_codes["123456"] = (123, time.monotonic() - 1)
        response = await self.http.post("/pair", json={"code": "123456"})
        self.assertEqual(response.status, 400)

    async def test_channel_change_without_pairing_and_user_separation(self):
        for user, channel in [(123, 456), (789, 999), (123, 555)]:
            interaction = SimpleNamespace(user=SimpleNamespace(id=user), channel_id=channel, response=SimpleNamespace(send_message=AsyncMock()))
            await bot.set_ocr_channel.callback(interaction)
            response = await self.event(user_id=str(user))
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["type"], "ack")
            self.discord.get_channel.assert_called_with(channel)
        self.assertEqual(database.execute("SELECT ocr_channel_id FROM users WHERE discord_user_id=789").fetchone()[0], 999)

    async def test_long_text_and_mentions(self):
        self.set_channel()
        text = "@everyone " + "x" * 4000
        response = await self.event(text=text)
        self.assertEqual((await response.json())["messages_sent"], 3)
        calls = self.channel.send.call_args_list
        self.assertEqual("".join(call.args[0] for call in calls), text)
        for call in calls:
            self.assertLessEqual(len(call.args[0]), 2000)
            self.assertEqual(call.kwargs["allowed_mentions"].to_dict()["parse"], [])

    async def test_invalid_requests_do_not_send(self):
        for payload in [[], None, {"type": "rune_detected"}, {"type": "chat_text", "user_id": True, "text": "hello"}]:
            response = await self.http.post("/event", json=payload)
            self.assertEqual(response.status, 400)
            self.assertFalse((await response.json())["ok"])
        response = await self.http.post("/event", data="{", headers={"Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        for changes in [{"text": " "}, {"text": 42}, {"text": "x" * 20001}, {"user_id": 2**80}]:
            response = await self.event(**changes)
            self.assertEqual(response.status, 400)
        self.channel.send.assert_not_called()

    async def test_missing_channel_and_disconnected(self):
        response = await self.event()
        self.assertEqual(response.status, 409)
        self.discord.is_ready.return_value = False
        response = await self.event()
        self.assertEqual(response.status, 503)
        self.channel.send.assert_not_called()

    async def test_channel_cache_miss(self):
        self.set_channel()
        self.discord.get_channel.return_value = None
        response = await self.event()
        self.assertEqual(response.status, 200)
        self.discord.fetch_channel.assert_awaited_once_with(456)

    async def test_delivery_errors_and_partial_delivery(self):
        self.set_channel()
        for exception, status in [(discord.Forbidden, 403), (discord.NotFound, 404), (discord.HTTPException, 502)]:
            self.channel.send.side_effect = exception(SimpleNamespace(status=status, reason="test"), "test")
            response = await self.event()
            self.assertEqual(response.status, status)
            self.assertFalse((await response.json())["ok"])
        self.channel.send.side_effect = [None, OSError("connection lost")]
        response = await self.event(text="x" * 2001)
        self.assertEqual(response.status, 502)
        self.assertEqual((await response.json())["messages_sent"], 1)


if __name__ == "__main__":
    unittest.main()
