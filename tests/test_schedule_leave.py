import importlib
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from schedule_store import initialize_schedule, create_event, add_owned_members, participating_events
from schedule_ui import ConfirmRemoval, ScheduleView


class LeaveCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = sqlite3.connect(':memory:')
        initialize_schedule(self.db)
        with patch('sqlite3.connect', return_value=self.db), patch('dotenv.load_dotenv'):
            self.bot = importlib.import_module('bot')
        self.patch = patch.object(self.bot, 'db', self.db)
        self.patch.start()
        self.event = create_event(self.db, 1, 'Limbo', 100)
        add_owned_members(self.db, self.event, 1, [2])

    async def asyncTearDown(self):
        self.patch.stop()
        self.db.close()

    def interaction(self, user):
        return SimpleNamespace(user=SimpleNamespace(id=user), response=SimpleNamespace(
            send_message=AsyncMock(), edit_message=AsyncMock()))

    async def test_member_leaves_only_self(self):
        request = self.interaction(2)
        await self.bot.leave_schedule_event.callback(request, str(self.event))
        self.assertEqual(participating_events(self.db, 2), [])
        self.assertEqual(len(participating_events(self.db, 1)), 1)
        self.assertTrue(request.response.send_message.call_args.kwargs['ephemeral'])

    async def test_owner_requires_confirmation(self):
        request = self.interaction(1)
        await self.bot.leave_schedule_event.callback(request, str(self.event))
        view = request.response.send_message.call_args.kwargs['view']
        self.assertIsInstance(view, ConfirmRemoval)
        self.assertEqual(len(participating_events(self.db, 2)), 1)
        await view.confirm.callback(self.interaction(2))
        self.assertEqual(len(participating_events(self.db, 2)), 1)
        await view.confirm.callback(self.interaction(1))
        self.assertEqual(participating_events(self.db, 2), [])
        view.schedule.stop()
        view.stop()

    async def test_invalid_unrelated_and_stale_choices(self):
        for value in ['Limbo', '-1', str(2**80), str(self.event)]:
            await self.bot.leave_schedule_event.callback(self.interaction(3), value)
        self.assertEqual(len(participating_events(self.db, 1)), 1)
        await self.bot.leave_schedule_event.callback(self.interaction(2), str(self.event))
        await self.bot.leave_schedule_event.callback(self.interaction(2), str(self.event))
        self.assertEqual(len(participating_events(self.db, 1)), 1)

    async def test_autocomplete_filters_limits_and_stable_values(self):
        create_event(self.db, 3, 'Private', 99)
        for i in range(30):
            create_event(self.db, 1, 'Boss ' + str(i), 200 + i)
        choices = await self.bot.leave_event_choices(self.interaction(1), '')
        self.assertEqual(len(choices), 25)
        self.assertEqual(choices[0].value, str(self.event))
        self.assertTrue(all(len(c.name) <= 100 for c in choices))
        self.assertEqual(await self.bot.leave_event_choices(self.interaction(1), 'Private'), [])
        self.assertEqual(len(await self.bot.leave_event_choices(self.interaction(2), 'limbo')), 1)

    async def test_leave_refreshes_member_and_owner_open_schedules(self):
        views = [ScheduleView(self.db, user) for user in (1, 2)]
        for view in views:
            view.message = SimpleNamespace(edit=AsyncMock())
        await self.bot.leave_schedule_event.callback(self.interaction(2), str(self.event))
        self.assertIn('No events', views[1].message.edit.call_args.kwargs['embed'].description)
        self.assertNotIn('<@2>', views[0].message.edit.call_args.kwargs['embed'].fields[0].value)
        for view in views:
            view.stop()

    async def test_owner_confirmation_refreshes_existing_schedule(self):
        existing = ScheduleView(self.db, 1)
        existing.message = SimpleNamespace(edit=AsyncMock())
        request = self.interaction(1)
        await self.bot.leave_schedule_event.callback(request, str(self.event))
        confirmation = request.response.send_message.call_args.kwargs['view']
        await confirmation.confirm.callback(self.interaction(1))
        self.assertIn('No events', existing.message.edit.call_args.kwargs['embed'].description)
        existing.stop()
        confirmation.schedule.stop()
        confirmation.stop()
