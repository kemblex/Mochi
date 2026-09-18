import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from schedule_store import (initialize_schedule, create_event, owned_event,
    owned_events, update_owned_event, participating_events, remove_owned_event)
from schedule_ui import ScheduleView, OwnedEventPicker, AddEventModal, ConfirmRemoval


def interaction(user=1):
    return SimpleNamespace(user=SimpleNamespace(id=user), response=SimpleNamespace(
        send_message=AsyncMock(), edit_message=AsyncMock(), send_modal=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()))


class EditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = sqlite3.connect(':memory:')
        initialize_schedule(self.db)
        self.event_id = create_event(self.db, 1, 'Limbo', 100)
        self.view = ScheduleView(self.db, 1)
        self.view.message = SimpleNamespace(edit=AsyncMock())

    async def asyncTearDown(self):
        self.view.stop()
        self.db.close()

    async def test_owned_picker_excludes_joined_and_paginates(self):
        other = create_event(self.db, 2, 'Kaling', 99)
        with self.db:
            self.db.execute('INSERT INTO event_members VALUES (?, ?)', (other, 1))
        for _ in range(26):
            create_event(self.db, 1, 'Limbo', 101)
        picker = OwnedEventPicker(self.view)
        self.assertTrue(picker.populate())
        self.assertEqual(len(picker.choose.options), 25)
        self.assertNotIn(str(other), [o.value for o in picker.choose.options])
        await picker.navigate(interaction(), 1)
        self.assertEqual(len(picker.choose.options), 2)
        picker.stop()

    async def test_edit_blank_time_preserves_latest_and_members(self):
        with self.db:
            self.db.execute('INSERT INTO event_members VALUES (?, ?)', (self.event_id, 2))
        modal = AddEventModal(self.view, owned_event(self.db, self.event_id, 1))
        self.assertFalse(modal.time_input.required)
        modal.boss_select._values = ['custom']
        modal.custom_name._value = 'Farm'
        modal.time_input._value = ''
        update_owned_event(self.db, self.event_id, 1, 'Limbo', 200)
        await modal.on_submit(interaction())
        self.assertEqual(owned_event(self.db, self.event_id, 1), (self.event_id, 1, 'Farm', 200))
        self.assertEqual(participating_events(self.db, 2)[0][0], self.event_id)
        modal.stop()

    async def test_owner_check_on_update_and_submission(self):
        self.assertFalse(update_owned_event(self.db, self.event_id, 2, 'No', 999))
        modal = AddEventModal(self.view, owned_event(self.db, self.event_id, 1))
        modal.boss_select._values = ['Seren']
        modal.time_input._value = '+2'
        await modal.on_submit(interaction(2))
        self.assertEqual(owned_event(self.db, self.event_id, 1)[2], 'Limbo')
        await modal.on_submit(interaction())
        self.assertEqual(owned_event(self.db, self.event_id, 1)[2], 'Seren')
        self.assertGreater(owned_event(self.db, self.event_id, 1)[3], 100)
        modal.stop()

    async def test_deleted_event_is_not_recreated(self):
        modal = AddEventModal(self.view, owned_event(self.db, self.event_id, 1))
        modal.boss_select._values = ['Limbo']
        modal.time_input._value = '+2'
        remove_owned_event(self.db, self.event_id, 1)
        await modal.on_submit(interaction())
        self.assertEqual(owned_events(self.db, 1), [])
        modal.stop()

    async def test_confirmation_cancel_permission_and_cascade(self):
        confirm = ConfirmRemoval(self.view, self.event_id)
        self.assertEqual(len(confirm.children), 2)
        await confirm.confirm.callback(interaction(2))
        self.assertIsNotNone(owned_event(self.db, self.event_id, 1))
        await confirm.cancel.callback(interaction())
        self.assertIsNotNone(owned_event(self.db, self.event_id, 1))
        confirm = ConfirmRemoval(self.view, self.event_id)
        await confirm.confirm.callback(interaction())
        self.assertEqual(owned_events(self.db, 1), [])
        self.assertEqual(self.db.execute('SELECT count(*) FROM event_members').fetchone()[0], 0)
        confirm.stop()
