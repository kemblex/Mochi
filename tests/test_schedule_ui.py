import importlib
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from schedule_store import initialize_schedule, create_event, participating_events
from schedule_ui import ScheduleView, AddEventModal


def interaction(user=1):
    return SimpleNamespace(user=SimpleNamespace(id=user), response=SimpleNamespace(
        send_message=AsyncMock(), edit_message=AsyncMock(), send_modal=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()))


class ScheduleUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = sqlite3.connect(':memory:')
        initialize_schedule(self.db)
        self.view = ScheduleView(self.db, 1)
        self.view.message = SimpleNamespace(edit=AsyncMock())

    async def asyncTearDown(self):
        self.view.stop()
        self.db.close()

    async def test_empty_schedule_and_pagination(self):
        self.assertIn('No events', self.view.embed().description)
        self.assertIn('Schedule for <@1>', self.view.embed().description)
        for _ in range(10):
            create_event(self.db, 1, 'Limbo', 100)
        self.assertEqual(len(self.view.embed().fields), 8)
        await self.view.next_page.callback(interaction())
        self.assertEqual(len(self.view.embed().fields), 2)
        self.assertTrue(self.view.next_page.disabled)

    async def test_other_user_cannot_create(self):
        modal = AddEventModal(self.view)
        modal.boss_select._values = ['Limbo']
        modal.time_input._value = '+2'
        await modal.on_submit(interaction(2))
        self.assertEqual(participating_events(self.db, 1), [])
        self.assertEqual(participating_events(self.db, 2), [])
        modal.stop()

    async def test_create_and_refresh_only_once(self):
        modal = AddEventModal(self.view)
        modal.boss_select._values = ['Limbo']
        modal.time_input._value = '+2'
        await modal.on_submit(interaction())
        await modal.on_submit(interaction())
        self.assertEqual(len(participating_events(self.db, 1)), 1)
        self.view.message.edit.assert_awaited_once()
        modal.stop()

    async def test_custom_and_invalid_input(self):
        modal = AddEventModal(self.view)
        modal.boss_select._values = ['custom']
        modal.custom_name._value = 'Limbo — Alt'
        modal.time_input._value = 'friday nonsense'
        await modal.on_submit(interaction())
        self.assertEqual(participating_events(self.db, 1), [])
        modal.time_input._value = 'friday +2'
        await modal.on_submit(interaction())
        self.assertEqual(participating_events(self.db, 1)[0][2], 'Limbo — Alt')
        modal.stop()

    async def test_mtime_never_saves(self):
        with patch('sqlite3.connect', return_value=self.db), patch('dotenv.load_dotenv'):
            bot = importlib.import_module('bot')
        with patch.object(bot, 'db', self.db):
            for label in (None, 'Limbo'):
                request = interaction()
                await bot.mtime.callback(request, '+2', label)
                self.assertIn('<t:', request.response.send_message.call_args.args[0])
            self.assertEqual(participating_events(self.db, 1), [])

    async def test_add_opens_one_modal(self):
        request = interaction()
        await self.view.add.callback(request)
        modal = request.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, AddEventModal)
        request.response.send_message.assert_not_called()
        components = modal.to_dict()['components']
        self.assertEqual([c['component']['type'] for c in components], [3, 4, 4])
        defaults = [o['value'] for o in components[0]['component']['options'] if o.get('default')]
        self.assertEqual(defaults, ['custom'])
        modal.stop()

    async def test_custom_fallback_and_preset_ignores_name(self):
        for choice, custom_name, expected in (
            ('custom', '', 'Event'),
            ('custom', '   ', 'Event'),
            ('custom', ' Farming ', 'Farming'),
            ('Limbo', 'Ignored text', 'Limbo'),
        ):
            modal = AddEventModal(self.view)
            modal.boss_select._values = [choice]
            modal.time_input._value = '+2'
            modal.custom_name._value = custom_name
            await modal.on_submit(interaction())
            self.assertEqual(participating_events(self.db, 1)[-1][2], expected)
            modal.stop()

    async def test_display_is_chronological_and_keeps_ids_internal(self):
        create_event(self.db, 1, 'Later', 200)
        shared_id = create_event(self.db, 2, 'Earlier', 100)
        with self.db:
            self.db.execute('INSERT INTO event_members VALUES (?, ?)', (shared_id, 1))
        embed = self.view.embed()
        self.assertEqual(embed.title, 'Boss Schedule')
        self.assertEqual(embed.description, 'Schedule for <@1>')
        self.assertEqual([field.name for field in embed.fields], ['Earlier', 'Later'])
        self.assertEqual(embed.fields[0].value, '<t:100:F> • <t:100:R>\nParty: <@1>, <@2>')
        self.assertNotIn('Owner:', str(embed.to_dict()))

    async def test_invalid_boss_is_rejected(self):
        for values in ([], ['invalid'], ['Limbo', 'Kaling']):
            modal = AddEventModal(self.view)
            modal.boss_select._values = values
            modal.time_input._value = '+2'
            await modal.on_submit(interaction())
            self.assertEqual(participating_events(self.db, 1), [])
            modal.stop()
