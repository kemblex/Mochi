import sqlite3
import tempfile
import unittest
from pathlib import Path
from schedule_store import initialize_schedule, create_event, participating_events, remove_owned_event


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        initialize_schedule(self.db)

    def tearDown(self):
        self.db.close()

    def test_duplicates_and_owner_membership(self):
        a = create_event(self.db, 1, 'Limbo', 100)
        b = create_event(self.db, 1, 'Limbo', 100)
        self.assertNotEqual(a, b)
        self.assertEqual([row[0] for row in participating_events(self.db, 1)], [a, b])
        self.assertEqual(participating_events(self.db, 2), [])

    def test_shared_event_permissions_and_cascade(self):
        event = create_event(self.db, 1, 'Limbo', 100)
        with self.db:
            self.db.execute('INSERT INTO event_members VALUES (?, ?)', (event, 2))
        self.assertEqual(participating_events(self.db, 1), participating_events(self.db, 2))
        self.assertIsNone(remove_owned_event(self.db, event, 2))
        self.assertEqual(remove_owned_event(self.db, event, 1), ('Limbo', 100))
        self.assertEqual(participating_events(self.db, 2), [])
        self.assertEqual(self.db.execute('SELECT count(*) FROM event_members').fetchone()[0], 0)
        self.assertGreater(create_event(self.db, 1, 'Limbo', 100), event)

    def test_atomic_creation(self):
        self.db.execute("CREATE TRIGGER reject_member BEFORE INSERT ON event_members BEGIN SELECT RAISE(ABORT, 'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            create_event(self.db, 1, 'Limbo', 100)
        self.assertEqual(self.db.execute('SELECT count(*) FROM scheduled_events').fetchone()[0], 0)

    def test_persistence_and_existing_users(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.db'
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE users(discord_user_id INTEGER PRIMARY KEY, ocr_channel_id INTEGER)')
            db.execute('INSERT INTO users VALUES (1, 123)')
            db.commit()
            initialize_schedule(db)
            event = create_event(db, 1, 'Limbo', 100)
            db.close()
            db = sqlite3.connect(path)
            initialize_schedule(db)
            self.assertEqual(participating_events(db, 1), [(event, 1, 'Limbo', 100)])
            self.assertEqual(db.execute('SELECT * FROM users').fetchall(), [(1, 123)])
            db.close()

    def test_sort_and_validation(self):
        create_event(self.db, 1, 'Later', 200)
        create_event(self.db, 1, 'Earlier', 100)
        self.assertEqual([r[2] for r in participating_events(self.db, 1)], ['Earlier', 'Later'])
        for name in (' ', 'x' * 101):
            with self.assertRaises(ValueError):
                create_event(self.db, 1, name, 100)
