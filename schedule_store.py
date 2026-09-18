"""SQLite storage shared by schedule commands and future UI controls."""


def initialize_schedule(db):
    # Must be enabled outside a transaction, on every connection.
    db.execute("PRAGMA foreign_keys = ON")
    with db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS event_members (
                event_id INTEGER NOT NULL REFERENCES scheduled_events(id) ON DELETE CASCADE,
                discord_user_id INTEGER NOT NULL,
                PRIMARY KEY (event_id, discord_user_id)
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS event_members_user ON event_members(discord_user_id)")


def create_event(db, owner_user_id, name, timestamp):
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("Event names must contain 1–100 characters.")
    # Both inserts commit together; a failed membership insert rolls back the event.
    with db:
        cursor = db.execute(
            "INSERT INTO scheduled_events(owner_user_id, name, timestamp) VALUES (?, ?, ?)",
            (owner_user_id, name, timestamp),
        )
        event_id = cursor.lastrowid
        db.execute("INSERT INTO event_members VALUES (?, ?)", (event_id, owner_user_id))
    return event_id


def participating_events(db, user_id):
    return db.execute("""
        SELECT e.id, e.owner_user_id, e.name, e.timestamp
        FROM scheduled_events AS e
        JOIN event_members AS m ON m.event_id = e.id
        WHERE m.discord_user_id = ?
        ORDER BY e.timestamp, e.id
    """, (user_id,)).fetchall()


def remove_owned_event(db, event_id, owner_user_id):
    with db:
        row = db.execute(
            "SELECT name, timestamp FROM scheduled_events WHERE id = ? AND owner_user_id = ?",
            (event_id, owner_user_id),
        ).fetchone()
        if row is not None:
            db.execute("DELETE FROM scheduled_events WHERE id = ? AND owner_user_id = ?", (event_id, owner_user_id))
    return row


def owned_events(db, user_id):
    return db.execute(
        'SELECT id, owner_user_id, name, timestamp FROM scheduled_events '
        'WHERE owner_user_id = ? ORDER BY timestamp, id', (user_id,)
    ).fetchall()


def owned_event(db, event_id, user_id):
    return db.execute(
        'SELECT id, owner_user_id, name, timestamp FROM scheduled_events '
        'WHERE id = ? AND owner_user_id = ?', (event_id, user_id)
    ).fetchone()


def update_owned_event(db, event_id, user_id, name, timestamp=None):
    """Update an owned event in place; None preserves its current saved time."""
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError('Event names must contain 1–100 characters.')
    with db:
        cursor = db.execute(
            'UPDATE scheduled_events SET name = ?, timestamp = COALESCE(?, timestamp) '
            'WHERE id = ? AND owner_user_id = ?', (name, timestamp, event_id, user_id)
        )
    return cursor.rowcount == 1


def participating_event(db, event_id, user_id):
    return db.execute('''
        SELECT e.id, e.owner_user_id, e.name, e.timestamp
        FROM scheduled_events e JOIN event_members m ON m.event_id = e.id
        WHERE e.id = ? AND m.discord_user_id = ?
    ''', (event_id, user_id)).fetchone()


def event_members(db, event_id):
    return [row[0] for row in db.execute(
        'SELECT discord_user_id FROM event_members WHERE event_id = ? ORDER BY discord_user_id',
        (event_id,),
    )]


def leave_event(db, event_id, user_id):
    """Leave only yourself; owners must use confirmed event cancellation instead."""
    with db:
        owner = db.execute('SELECT owner_user_id FROM scheduled_events WHERE id = ?', (event_id,)).fetchone()
        if owner is None:
            return 'missing'
        if owner[0] == user_id:
            return 'owner_confirmation_required'
        cursor = db.execute('DELETE FROM event_members WHERE event_id = ? AND discord_user_id = ?', (event_id, user_id))
    return 'left' if cursor.rowcount else 'not_member'


def add_owned_members(db, event_id, owner_id, user_ids):
    """Owner-only batch addition; existing memberships are left unchanged."""
    with db:
        if owned_event(db, event_id, owner_id) is None:
            raise PermissionError('Event no longer exists or you are not its owner.')
        added = 0
        for user_id in set(user_ids):
            added += db.execute(
                'INSERT INTO event_members(event_id, discord_user_id) VALUES (?, ?) '
                'ON CONFLICT(event_id, discord_user_id) DO NOTHING', (event_id, user_id)
            ).rowcount
    return added


def remove_owned_members(db, event_id, owner_id, user_ids):
    """Owner-only removal; never remove the owner through membership management."""
    with db:
        if owned_event(db, event_id, owner_id) is None:
            raise PermissionError('Event no longer exists or you are not its owner.')
        ids = set(user_ids)
        if owner_id in ids:
            raise ValueError('Use Leave event to cancel your own event with confirmation.')
        removed = 0
        for user_id in ids:
            removed += db.execute(
                'DELETE FROM event_members WHERE event_id = ? AND discord_user_id = ?',
                (event_id, user_id),
            ).rowcount
    return removed
