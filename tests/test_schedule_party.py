import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from schedule_store import (initialize_schedule, create_event, participating_events,
                            event_members, leave_event, owned_event, remove_owned_event)
from schedule_ui import ScheduleView, PartyView, OwnedEventPicker, ConfirmRemoval


def interaction(user=1):
    return SimpleNamespace(user=SimpleNamespace(id=user), response=SimpleNamespace(
        send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
        edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()))


class PartyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = sqlite3.connect(':memory:')
        initialize_schedule(self.db)
        self.event_id = create_event(self.db, 1, 'Limbo', 100)
        with self.db:
            self.db.execute('INSERT INTO event_members VALUES (?, ?)', (self.event_id, 2))
        self.schedule = ScheduleView(self.db, 2)
        self.schedule.message = SimpleNamespace(edit=AsyncMock())

    async def asyncTearDown(self):
        self.schedule.stop()
        self.db.close()

    async def test_party_picker_includes_joined_event(self):
        picker = OwnedEventPicker(self.schedule, party_mode=True)
        self.assertTrue(picker.populate())
        self.assertEqual(picker.choose.options[0].value, str(self.event_id))
        picker.choose._values = [str(self.event_id)]
        request = interaction(2)
        await picker.choose.callback(request)
        party = request.response.edit_message.call_args.kwargs['view']
        self.assertIn('Party (2)', party.embed().description)
        self.assertIn('<@1>', party.embed().description)
        self.assertEqual(request.response.edit_message.call_args.kwargs['allowed_mentions'].to_dict()['parse'], [])
        party.stop()
        picker.stop()

    async def test_nonowner_leave_preserves_shared_event(self):
        party = PartyView(self.schedule, self.event_id)
        await party.leave.callback(interaction(2))
        self.assertEqual(participating_events(self.db, 2), [])
        self.assertIsNotNone(owned_event(self.db, self.event_id, 1))
        self.assertEqual(event_members(self.db, self.event_id), [1])
        self.assertEqual(leave_event(self.db, self.event_id, 2), 'not_member')
        self.schedule.message.edit.assert_awaited_once()
        party.stop()

    async def test_owner_leave_requires_confirmation(self):
        self.schedule.user_id = 1
        party = PartyView(self.schedule, self.event_id)
        request = interaction(1)
        await party.leave.callback(request)
        self.assertEqual(event_members(self.db, self.event_id), [1, 2])
        confirm = request.response.send_message.call_args.kwargs['view']
        self.assertIsInstance(confirm, ConfirmRemoval)
        await confirm.confirm.callback(interaction(1))
        self.assertEqual(event_members(self.db, self.event_id), [])
        party.stop()

    async def test_other_user_and_stale_party(self):
        party = PartyView(self.schedule, self.event_id)
        await party.leave.callback(interaction(1))
        self.assertEqual(event_members(self.db, self.event_id), [1, 2])
        remove_owned_event(self.db, self.event_id, 1)
        self.assertIsNone(party.embed())
        self.assertEqual(leave_event(self.db, self.event_id, 2), 'missing')
        await party.refresh_display(interaction(2))
        party.stop()

    async def test_member_pagination(self):
        with self.db:
            self.db.executemany('INSERT INTO event_members VALUES (?, ?)', [(self.event_id, i) for i in range(3, 40)])
        party = PartyView(self.schedule, self.event_id)
        self.assertFalse(party.embed() is None)
        self.assertFalse(party.next_page.disabled)
        party.page = 1
        self.assertIn('<@39>', party.embed().description)
        self.assertTrue(party.next_page.disabled)
        party.stop()

    async def test_main_party_button_opens_picker(self):
        self.assertEqual([item.label for item in self.schedule.children[:3]], ['Add', 'Edit', 'Party'])
        request = interaction(2)
        await self.schedule.party.callback(request)
        picker = request.response.send_message.call_args.kwargs['view']
        self.assertTrue(picker.party_mode)
        self.assertEqual(picker.choose.options[0].value, str(self.event_id))
        picker.stop()

    async def test_owner_membership_helpers_and_shared_visibility(self):
        from schedule_store import add_owned_members, remove_owned_members
        self.assertEqual(add_owned_members(self.db, self.event_id, 1, [2, 3, 3]), 1)
        self.assertEqual(participating_events(self.db, 3)[0][0], self.event_id)
        with self.assertRaises(PermissionError):
            add_owned_members(self.db, self.event_id, 2, [4])
        with self.assertRaises(PermissionError):
            remove_owned_members(self.db, self.event_id, 2, [3])
        with self.assertRaises(ValueError):
            remove_owned_members(self.db, self.event_id, 1, [3, 1])
        self.assertIn(3, event_members(self.db, self.event_id))
        self.assertEqual(remove_owned_members(self.db, self.event_id, 1, [3, 3]), 1)
        self.assertEqual(remove_owned_members(self.db, self.event_id, 1, [3]), 0)
        self.assertIsNotNone(owned_event(self.db, self.event_id, 1))

    async def test_owner_controls_and_dm_explanation(self):
        party = PartyView(self.schedule, self.event_id)
        self.assertNotIn('Add Member', [item.label for item in party.children])
        party.stop()
        self.schedule.user_id = 1
        party = PartyView(self.schedule, self.event_id)
        self.assertIn('Add Member', [item.label for item in party.children])
        self.assertIn('Remove Member', [item.label for item in party.children])
        request = interaction(1)
        request.guild = None
        await party.add_members.callback(request)
        self.assertIn('server', request.response.send_message.call_args.args[0])
        party.stop()

    async def test_add_remove_ui_and_stale_owner_checks(self):
        from schedule_ui import AddMembersView, RemoveMembersView
        from unittest.mock import MagicMock
        self.schedule.user_id = 1
        party = PartyView(self.schedule, self.event_id)
        request = interaction(1)
        request.guild = MagicMock()
        request.guild.get_member.return_value = None
        request.guild.fetch_member = AsyncMock(return_value=SimpleNamespace(display_name='Friend', name='friend'))
        request.client = MagicMock()
        request.client.get_user.return_value = None
        request.client.fetch_user = AsyncMock(return_value=SimpleNamespace(display_name='Friend', name='friend'))
        await party.add_members.callback(request)
        add = request.response.edit_message.call_args.kwargs['view']
        self.assertIsInstance(add, AddMembersView)
        add.choose._values = [SimpleNamespace(id=3, bot=False)]
        await add.choose.callback(request)
        self.assertIn(3, event_members(self.db, self.event_id))
        await party.remove_members.callback(request)
        remove = request.edit_original_response.call_args.kwargs['view']
        self.assertIsInstance(remove, RemoveMembersView)
        self.assertNotIn('1', [option.value for option in remove.choose.options])
        remove.choose._values = ['3']
        await remove.choose.callback(request)
        self.assertNotIn(3, event_members(self.db, self.event_id))
        remove_owned_event(self.db, self.event_id, 1)
        await add.choose.callback(request)
        self.assertEqual(event_members(self.db, self.event_id), [])
        party.stop()

    async def test_removal_picker_paginates(self):
        from schedule_store import add_owned_members
        from schedule_ui import RemoveMembersView
        from unittest.mock import MagicMock
        add_owned_members(self.db, self.event_id, 1, range(3, 35))
        self.schedule.user_id = 1
        party = PartyView(self.schedule, self.event_id)
        request = interaction(1)
        request.guild = None
        request.client = MagicMock()
        request.client.get_user.return_value = None
        request.client.fetch_user = AsyncMock(return_value=SimpleNamespace(display_name='Friend', name='friend'))
        picker = RemoveMembersView(party, request)
        self.assertTrue(picker.populate())
        self.assertEqual(len(picker.choose.options), 25)
        await picker.navigate(request, 1)
        self.assertEqual(len(picker.choose.options), 8)
        self.assertTrue(picker.next_page.disabled)
        picker.stop()
        party.stop()

    async def test_remove_fetches_name_and_updates_originating_schedule(self):
        from schedule_ui import RemoveMembersView
        from unittest.mock import MagicMock
        self.schedule.user_id = 1
        party = PartyView(self.schedule, self.event_id)
        request = interaction(1)
        request.guild = MagicMock()
        request.guild.get_member.return_value = None
        request.guild.fetch_member = AsyncMock(return_value=SimpleNamespace(display_name='Alex', name='alex'))
        request.client = MagicMock()
        request.client.get_user.return_value = None
        await party.remove_members.callback(request)
        picker = request.edit_original_response.call_args.kwargs['view']
        self.assertEqual(picker.choose.options[0].label, 'Alex')
        self.assertEqual(picker.choose.options[0].description, '@alex')
        request.guild.fetch_member.assert_awaited_once_with(2)
        picker.choose._values = ['2']
        await picker.choose.callback(request)
        updated = self.schedule.message.edit.call_args.kwargs['embed']
        self.assertIn('Party: <@1>', updated.fields[0].value)
        self.assertNotIn('<@2>', updated.fields[0].value)
        self.assertNotIn('Refresh', [item.label for item in self.schedule.children])
        self.assertNotIn('Refresh', [item.label for item in party.children])
        party.stop()
