"""Schedule display and event creation; time rules live in time_parser.py."""
import asyncio
import datetime
import weakref
import logging
import discord
from schedule_store import (create_event, participating_events, owned_events,
                            owned_event, update_owned_event, remove_owned_event,
                            participating_event, event_members, leave_event,
                            add_owned_members, remove_owned_members)
from time_parser import parse_mtime
from boss_data import BOSSES

PAGE_SIZE = 8
_open_schedules = weakref.WeakSet()


async def refresh_open_schedules(db):
    """Refresh active schedule messages from this process without pinging members."""
    for view in list(_open_schedules):
        if view.db is not db or view.message is None or view.is_finished():
            continue
        try:
            await view.message.edit(embed=view.embed(), view=view,
                                    allowed_mentions=discord.AllowedMentions.none())
        except (discord.HTTPException, OSError):
            logging.getLogger(__name__).warning('Could not refresh a schedule message', exc_info=True)



class ScheduleView(discord.ui.View):
    def __init__(self, db, user_id):
        super().__init__(timeout=600)
        self.db = db
        self.user_id = user_id
        self.page = 0
        self.message = None
        _open_schedules.add(self)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message('Run /mschedule to manage your own schedule.', ephemeral=True)
            return False
        return True

    def embed(self):
        events = participating_events(self.db, self.user_id)
        pages = max(1, (len(events) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = min(max(0, self.page), pages - 1)
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page == pages - 1
        embed = discord.Embed(
            title='Boss Schedule',
            description=f'Schedule for <@{self.user_id}>',
            color=discord.Color.blurple()
            )
        if not events:
            embed.description += '\n\nNo events yet. Use Add to create one.'
        for event_id, owner_id, name, timestamp in events[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]:
            members = event_members(self.db, event_id)
            # Keep eight fields comfortably below Discord's embed size limits.
            party = ', '.join(f'<@{uid}>' for uid in members[:12])
            if len(members) > 12:
                party += f' … (+{len(members) - 12} more; see Party)'
            embed.add_field(name=discord.utils.escape_markdown(name),
                            value=f'<t:{timestamp}:F> • <t:{timestamp}:R>\nParty: {party}', inline=False)
        embed.set_footer(text=f'Page {self.page + 1}/{pages} • Controls expire after 10 minutes; run /mschedule again.')
        return embed

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label='Add', style=discord.ButtonStyle.success)
    async def add(self, interaction, button):
        await interaction.response.send_modal(AddEventModal(self))

    @discord.ui.button(label='Edit', style=discord.ButtonStyle.primary)
    async def edit(self, interaction, button):
        picker = OwnedEventPicker(self)
        if not picker.populate():
            await interaction.response.send_message('You do not own any events yet.', ephemeral=True)
            return
        await interaction.response.send_message('Choose an event you own:', view=picker, ephemeral=True)

    @discord.ui.button(label='Party', style=discord.ButtonStyle.primary, row=0)
    async def party(self, interaction, button):
        picker = OwnedEventPicker(self, party_mode=True)
        if not picker.populate():
            await interaction.response.send_message('You are not participating in any events yet.', ephemeral=True)
            return
        await interaction.response.send_message('Choose an event to view its party:', view=picker, ephemeral=True)

    @discord.ui.button(label='Prev', row=0)
    async def previous(self, interaction, button):
        self.page -= 1
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label='Next', row=0)
    async def next_page(self, interaction, button):
        self.page += 1
        await interaction.response.edit_message(embed=self.embed(), view=self)


class AddEventModal(discord.ui.Modal):
    def __init__(self, schedule, event=None):
        super().__init__(title='Edit scheduled event' if event else 'Add scheduled event', timeout=300)
        self.event_id = event[0] if event else None
        selected_boss = event[2] if event and event[2] in BOSSES else 'custom'
        self.schedule = schedule
        self.saved = False
        self.boss_select = discord.ui.Select(
            placeholder='Choose a boss', min_values=1, max_values=1,
            options=[
                *[discord.SelectOption(label=name, value=name, default=name == selected_boss) for name in BOSSES],
                discord.SelectOption(label='Custom…', value='custom', default=selected_boss == 'custom'),
            ],
        )
        self.add_item(discord.ui.Label(text='Boss', component=self.boss_select))
        self.time_input = discord.ui.TextInput(
            placeholder='Leave blank to keep the current time' if event else '+2, tomorrow +1.5, friday +2',
            required=event is None, max_length=100
        )
        self.add_item(discord.ui.Label(text='Reset-relative time', component=self.time_input))
        self.custom_name = discord.ui.TextInput(
            required=False, max_length=100,
            default=event[2] if event and selected_boss == 'custom' else None,
        )
        self.add_item(discord.ui.Label(
            text='Custom name', description='Optional for Custom — leave blank to name it Event.',
            component=self.custom_name,
        ))

    async def on_submit(self, interaction):
        if not await self.schedule.interaction_check(interaction):
            return
        if self.saved:
            await interaction.response.send_message('This form has already been saved.', ephemeral=True)
            return
        try:
            timestamp = (None if self.event_id is not None and not self.time_input.value.strip()
                         else parse_mtime(self.time_input.value))
            choices = self.boss_select.values
            if len(choices) != 1 or choices[0] not in (*BOSSES, 'custom'):
                raise ValueError('Choose a boss or Custom.')
            boss = choices[0]
            if boss == 'custom':
                name = self.custom_name.value.strip() or 'Event'
            else:
                name = boss
            if self.event_id is None:
                event_id = create_event(self.schedule.db, interaction.user.id, name, timestamp)
            else:
                if not update_owned_event(self.schedule.db, self.event_id, interaction.user.id, name, timestamp):
                    await interaction.response.send_message('Event no longer exists or you are not its owner.', ephemeral=True)
                    return
                event_id = self.event_id
        except ValueError as error:
            await interaction.response.send_message(f'{error} Open the form again to retry.', ephemeral=True)
            return
        self.saved = True
        action = 'Updated' if self.event_id is not None else 'Created'
        await interaction.response.send_message(f'{action} event.', ephemeral=True)
        if self.schedule.message is not None:
            try:
                await self.schedule.message.edit(embed=self.schedule.embed(), view=self.schedule)
            except discord.HTTPException:
                await interaction.followup.send('Event saved. Run /mschedule again to see it.', ephemeral=True)


class OwnedEventPicker(discord.ui.View):
    """Paginated event selection for Edit or Party; values are stable IDs."""
    def __init__(self, schedule, party_mode=False):
        super().__init__(timeout=300)
        self.schedule = schedule
        self.party_mode = party_mode
        self.choose.placeholder = 'Choose a participating event' if party_mode else 'Choose an event you own'
        self.page = 0

    async def interaction_check(self, interaction):
        return await self.schedule.interaction_check(interaction)

    def populate(self):
        query = participating_events if self.party_mode else owned_events
        events = query(self.schedule.db, self.schedule.user_id)
        if not events:
            return False
        pages = (len(events) + 24) // 25
        self.page = min(max(self.page, 0), pages - 1)
        self.choose.options = [discord.SelectOption(
            label=name, value=str(event_id),
            description=datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        ) for event_id, owner_id, name, timestamp in events[self.page * 25:(self.page + 1) * 25]]
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page == pages - 1
        return True

    async def navigate(self, interaction, delta):
        self.page += delta
        if not self.populate():
            await interaction.response.edit_message(content='No events are available for this action.', view=None)
            return
        await interaction.response.edit_message(content=f'Choose an event (page {self.page + 1}):', view=self)

    @discord.ui.select(placeholder='Choose an event you own')
    async def choose(self, interaction, select):
        event_id = int(select.values[0])
        if self.party_mode:
            party = PartyView(self.schedule, event_id)
            embed = party.embed()
            if embed is None:
                await interaction.response.send_message('Event unavailable or you are no longer a participant.', ephemeral=True)
                return
            await interaction.response.edit_message(content=None, embed=embed, view=party,
                                                    allowed_mentions=discord.AllowedMentions.none())
            return
        event = owned_event(self.schedule.db, event_id, interaction.user.id)
        if event is None:
            await interaction.response.send_message('Event no longer exists or you are not its owner.', ephemeral=True)
            return
        await interaction.response.edit_message(
            content=None, embed=management_embed(event), view=ManageEventView(self.schedule, event_id)
        )

    @discord.ui.button(label='Previous', row=1)
    async def previous(self, interaction, button):
        await self.navigate(interaction, -1)

    @discord.ui.button(label='Next', row=1)
    async def next_page(self, interaction, button):
        await self.navigate(interaction, 1)


def management_embed(event):
    return discord.Embed(title='Manage event', description=(
        f'**{discord.utils.escape_markdown(event[2])}**\n<t:{event[3]}:F> • <t:{event[3]}:R>'
    ))


class ManageEventView(discord.ui.View):
    def __init__(self, schedule, event_id):
        super().__init__(timeout=300)
        self.schedule = schedule
        self.event_id = event_id

    async def interaction_check(self, interaction):
        if not await self.schedule.interaction_check(interaction):
            return False
        if owned_event(self.schedule.db, self.event_id, interaction.user.id) is None:
            await interaction.response.send_message('Event no longer exists or you are not its owner.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Edit Event', style=discord.ButtonStyle.primary)
    async def edit(self, interaction, button):
        event = owned_event(self.schedule.db, self.event_id, interaction.user.id)
        if event is None:
            await interaction.response.send_message('Event no longer exists or you are not its owner.', ephemeral=True)
            return
        await interaction.response.send_modal(AddEventModal(self.schedule, event))

    @discord.ui.button(label='Remove Event', style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        await interaction.response.send_message(
            'Cancel this shared event for everyone? This removes all party memberships too.',
            view=ConfirmRemoval(self.schedule, self.event_id), ephemeral=True,
        )


class ConfirmRemoval(ManageEventView):
    def __init__(self, schedule, event_id):
        super().__init__(schedule, event_id)
        # Only show confirmation actions, not the inherited management buttons.
        self.remove_item(self.edit)
        self.remove_item(self.remove)

    @discord.ui.button(label='Confirm cancellation', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if not await self.schedule.interaction_check(interaction):
            return
        removed = remove_owned_event(self.schedule.db, self.event_id, interaction.user.id)
        text = 'Event cancelled.' if removed else 'Event no longer exists or you are not its owner.'
        await interaction.response.edit_message(content=text, view=None)
        self.stop()
        if removed:
            await refresh_open_schedules(self.schedule.db)

    @discord.ui.button(label='Keep event')
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content='Cancellation dismissed.', view=None)
        self.stop()


class PartyView(discord.ui.View):
    """Private party controls for the invoking schedule user."""
    def __init__(self, schedule, event_id):
        super().__init__(timeout=300)
        self.schedule = schedule
        self.event_id = event_id
        self.page = 0
        if owned_event(schedule.db, event_id, schedule.user_id) is None:
            self.remove_item(self.add_members)
            self.remove_item(self.remove_members)

    async def interaction_check(self, interaction):
        return await self.schedule.interaction_check(interaction)

    def embed(self):
        event = participating_event(self.schedule.db, self.event_id, self.schedule.user_id)
        if event is None:
            return None
        members = event_members(self.schedule.db, self.event_id)
        pages = max(1, (len(members) + 29) // 30)
        self.page = max(0, min(self.page, pages - 1))
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page == pages - 1
        shown = '\n'.join(f'<@{user_id}>' for user_id in members[self.page * 30:(self.page + 1) * 30])
        embed = discord.Embed(title='Event party', description=(
            f'**{discord.utils.escape_markdown(event[2])}**\n<t:{event[3]}:F> • <t:{event[3]}:R>\n'
            f'Host: <@{event[1]}>\n\nParty ({len(members)}):\n{shown}'
        ))
        embed.set_footer(text=f'Member page {self.page + 1}/{pages}')
        return embed

    async def refresh_display(self, interaction, delta=0):
        self.page += delta
        embed = self.embed()
        if embed is None:
            await interaction.response.edit_message(content='Event unavailable or you are no longer a participant.', embed=None, view=None)
            return
        await interaction.response.edit_message(embed=embed, view=self, allowed_mentions=discord.AllowedMentions.none())

    async def check_owner(self, interaction):
        if not await self.schedule.interaction_check(interaction):
            return False
        if owned_event(self.schedule.db, self.event_id, interaction.user.id) is None:
            await interaction.response.send_message('Event no longer exists or you are not its owner.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Add Member', style=discord.ButtonStyle.success)
    async def add_members(self, interaction, button):
        if not await self.check_owner(interaction):
            return
        if interaction.guild is None:
            await interaction.response.send_message(
                'Use Add Member from /mschedule in a server shared with those users. Discord’s user picker cannot select arbitrary friends in DMs.',
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(content='Select users to add:', embed=None, view=AddMembersView(self))

    @discord.ui.button(label='Remove Member', style=discord.ButtonStyle.danger)
    async def remove_members(self, interaction, button):
        if not await self.check_owner(interaction):
            return
        picker = RemoveMembersView(self, interaction)
        if not picker.populate():
            await interaction.response.send_message('There are no other members to remove.', ephemeral=True)
            return
        await interaction.response.defer()
        await picker.resolve_names()
        await interaction.edit_original_response(content='Select members to remove:', embed=None, view=picker)

    @discord.ui.button(label='Leave event', style=discord.ButtonStyle.danger)
    async def leave(self, interaction, button):
        if not await self.schedule.interaction_check(interaction):
            return
        result = leave_event(self.schedule.db, self.event_id, interaction.user.id)
        if result == 'owner_confirmation_required':
            await interaction.response.send_message(
                'You own this event. Leaving will cancel it for everyone. Confirm cancellation?',
                view=ConfirmRemoval(self.schedule, self.event_id), ephemeral=True,
            )
            return
        messages = {'left': 'You left the event. It remains scheduled for the other participants.',
                    'missing': 'This event no longer exists.', 'not_member': 'You are not a member of this event.'}
        await interaction.response.edit_message(content=messages[result], embed=None, view=None)
        self.stop()
        if self.schedule.message is not None:
            try:
                await self.schedule.message.edit(embed=self.schedule.embed(), view=self.schedule)
            except discord.HTTPException:
                await interaction.followup.send('Run /mschedule again to refresh your schedule.', ephemeral=True)

    @discord.ui.button(label='Previous members', row=1)
    async def previous(self, interaction, button):
        await self.refresh_display(interaction, -1)

    @discord.ui.button(label='Next members', row=1)
    async def next_page(self, interaction, button):
        await self.refresh_display(interaction, 1)


class MemberManagementView(discord.ui.View):
    def __init__(self, party):
        super().__init__(timeout=300)
        self.party = party

    async def interaction_check(self, interaction):
        return await self.party.check_owner(interaction)

    async def finish(self, interaction, message):
        await interaction.response.edit_message(
            content=message, embed=self.party.embed(), view=self.party,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.stop()
        if self.party.schedule.message is not None:
            try:
                await self.party.schedule.message.edit(
                    embed=self.party.schedule.embed(), view=self.party.schedule,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                await interaction.followup.send('Members saved. Run /mschedule for an updated schedule.', ephemeral=True)

    @discord.ui.button(label='Back to Party', row=2)
    async def back(self, interaction, button):
        await self.finish(interaction, None)


class AddMembersView(MemberManagementView):
    @discord.ui.select(cls=discord.ui.UserSelect, placeholder='Select users to add', max_values=25, row=0)
    async def choose(self, interaction, select):
        if not await self.party.check_owner(interaction):
            return
        if interaction.guild is None:
            await interaction.response.send_message('Add members from a server, not a DM.', ephemeral=True)
            return
        if any(user.bot for user in select.values):
            await interaction.response.send_message('Select people rather than bot accounts.', ephemeral=True)
            return
        try:
            count = add_owned_members(self.party.schedule.db, self.party.event_id,
                                      interaction.user.id, [user.id for user in select.values])
        except PermissionError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self.finish(interaction, f'Added {count} member(s). Existing members were unchanged.')


class RemoveMembersView(MemberManagementView):
    def __init__(self, party, interaction):
        super().__init__(party)
        self.page = 0
        self.guild = interaction.guild
        self.client = interaction.client
        self.resolved_users = {}

    def populate(self):
        members = [uid for uid in event_members(self.party.schedule.db, self.party.event_id)
                   if uid != self.party.schedule.user_id]
        if not members:
            return False
        pages = (len(members) + 24) // 25
        self.page = max(0, min(self.page, pages - 1))
        self.choose.options = []
        for uid in members[self.page * 25:(self.page + 1) * 25]:
            user = self.resolved_users.get(uid) or (self.guild.get_member(uid) if self.guild else None) or self.client.get_user(uid)
            label = str(user.display_name) if user else f'User {uid}'
            self.choose.options.append(discord.SelectOption(label=label[:100], value=str(uid), description=f'@{user.name}'[:100] if user else 'Name unavailable'))
        self.choose.max_values = len(self.choose.options)
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page == pages - 1
        return True

    async def resolve_names(self):
        async def resolve(uid):
            user = self.resolved_users.get(uid) or (self.guild.get_member(uid) if self.guild else None) or self.client.get_user(uid)
            if user is None and self.guild is not None:
                try:
                    user = await asyncio.wait_for(self.guild.fetch_member(uid), timeout=8)
                except (discord.HTTPException, OSError, TimeoutError):
                    pass
            if user is None:
                try:
                    user = await asyncio.wait_for(self.client.fetch_user(uid), timeout=8)
                except (discord.HTTPException, OSError, TimeoutError):
                    pass
            if user is not None:
                self.resolved_users[uid] = user
        await asyncio.gather(*(resolve(int(option.value)) for option in self.choose.options))
        self.populate()

    @discord.ui.select(placeholder='Select existing members to remove', row=0)
    async def choose(self, interaction, select):
        if not await self.party.check_owner(interaction):
            return
        try:
            count = remove_owned_members(self.party.schedule.db, self.party.event_id,
                                         interaction.user.id, [int(uid) for uid in select.values])
        except (PermissionError, ValueError) as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self.finish(interaction, f'Removed {count} member(s). The event is still scheduled.')

    async def navigate(self, interaction, delta):
        self.page += delta
        if not self.populate():
            await self.finish(interaction, 'There are no other members to remove.')
            return
        await interaction.response.defer()
        await self.resolve_names()
        await interaction.edit_original_response(content=f'Select members to remove (page {self.page + 1}):', view=self)

    @discord.ui.button(label='Previous', row=1)
    async def previous(self, interaction, button):
        await self.navigate(interaction, -1)

    @discord.ui.button(label='Next', row=1)
    async def next_page(self, interaction, button):
        await self.navigate(interaction, 1)
