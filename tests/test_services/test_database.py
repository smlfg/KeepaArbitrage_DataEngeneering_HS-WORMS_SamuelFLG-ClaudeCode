"""
Tests for database CRUD operations in src/services/database.py

Uses SQLite in-memory via the fixtures in conftest.py:
- `db_session` fixture provides an async SQLAlchemy session
- The session automatically rolls back after each test
"""

import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.services.database import (
    create_watch,
    get_user_watches,
    get_active_watches,
    get_active_watch_count,
    update_watch_price,
    create_price_alert,
    get_pending_alerts,
    mark_alert_sent,
    soft_delete_watch,
    get_user_by_id,
    User,
    WatchedProduct,
    PriceHistory,
    PriceAlert,
    WatchStatus,
    AlertStatus,
)


# Helper fixture to create a test user
@pytest_asyncio.fixture
async def test_user(db_session):
    """Create a test user for watch tests."""
    user = User(
        id=uuid.uuid4(),
        email=f"test_{uuid.uuid4().hex[:8]}@example.com",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def test_user2(db_session):
    """Create a second test user for multi-user tests."""
    user = User(
        id=uuid.uuid4(),
        email=f"test2_{uuid.uuid4().hex[:8]}@example.com",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


class TestCreateWatch:
    """Tests for create_watch function."""

    async def test_create_watch_basic(self, test_user):
        """Test 1: create_watch creates a watch row with correct fields."""
        user_id = str(test_user.id)
        asin = "B08N5WRWNW"
        target_price = 100.0

        watch = await create_watch(
            user_id=user_id,
            asin=asin,
            target_price=target_price,
        )

        assert watch is not None
        assert watch.user_id == test_user.id
        assert watch.asin == asin
        assert watch.target_price == target_price
        assert watch.current_price is None
        assert watch.status == WatchStatus.ACTIVE
        assert isinstance(watch.id, uuid.UUID)

    async def test_create_watch_with_optional_fields(self, test_user):
        """Test 2: create_watch with optional current_price field."""
        user_id = str(test_user.id)
        asin = "B08N5WRWNW"
        target_price = 100.0
        current_price = 150.0

        watch = await create_watch(
            user_id=user_id,
            asin=asin,
            target_price=target_price,
            current_price=current_price,
        )

        assert watch is not None
        assert watch.current_price == current_price
        assert watch.target_price == target_price
        assert watch.asin == asin


class TestGetUserWatches:
    """Tests for get_user_watches function."""

    async def test_get_user_watches_empty(self, test_user):
        """Test 3: get_user_watches returns empty list for user with no watches."""
        user_id = str(test_user.id)

        watches = await get_user_watches(user_id)

        assert watches == []
        assert isinstance(watches, list)

    async def test_get_user_watches_returns_watches(self, test_user):
        """Test 4: get_user_watches returns watches for a user."""
        user_id = str(test_user.id)

        # Create two watches for the user
        watch1 = await create_watch(
            user_id=user_id,
            asin="B08N5WRWNW",
            target_price=100.0,
        )
        watch2 = await create_watch(
            user_id=user_id,
            asin="B08N5M7S6K",
            target_price=200.0,
        )

        watches = await get_user_watches(user_id)

        assert len(watches) == 2
        watch_ids = {w.id for w in watches}
        assert watch1.id in watch_ids
        assert watch2.id in watch_ids


class TestGetActiveWatches:
    """Tests for get_active_watches function."""

    async def test_get_active_watches_returns_active(self, test_user, test_user2):
        """Test 5: get_active_watches returns only active watches."""
        # Create active watches for both users
        watch1 = await create_watch(
            user_id=str(test_user.id),
            asin="B08N5WRWNW",
            target_price=100.0,
        )
        watch2 = await create_watch(
            user_id=str(test_user2.id),
            asin="B08N5M7S6K",
            target_price=200.0,
        )

        active_watches = await get_active_watches()

        assert len(active_watches) == 2
        watch_ids = {w.id for w in active_watches}
        assert watch1.id in watch_ids
        assert watch2.id in watch_ids

    async def test_get_active_watches_excludes_inactive(self, test_user):
        """Test 6: get_active_watches excludes inactive (soft-deleted) watches."""
        # Create an active watch
        active_watch = await create_watch(
            user_id=str(test_user.id),
            asin="B08N5WRWNW",
            target_price=100.0,
        )

        # Create and soft-delete another watch
        inactive_watch = await create_watch(
            user_id=str(test_user.id),
            asin="B08N5M7S6K",
            target_price=200.0,
        )
        await soft_delete_watch(str(inactive_watch.id), str(test_user.id))

        active_watches = await get_active_watches()

        assert len(active_watches) == 1
        assert active_watches[0].id == active_watch.id


class TestUpdateWatchPrice:
    """Tests for update_watch_price function."""

    async def test_update_watch_price_creates_history(self, test_user, db_session):
        """Test 7: update_watch_price updates current_price and creates PriceHistory entry."""
        # Create a watch
        watch = await create_watch(
            user_id=str(test_user.id),
            asin="B08N5WRWNW",
            target_price=100.0,
            current_price=150.0,
        )

        # Update the price
        new_price = 120.0
        seller = "Amazon"
        updated_watch = await update_watch_price(
            str(watch.id),
            new_price,
            buy_box_seller=seller,
        )

        # Verify watch was updated
        assert updated_watch is not None
        assert updated_watch.current_price == new_price
        assert updated_watch.last_checked_at is not None

        # Verify PriceHistory entry was created
        result = await db_session.execute(
            select(PriceHistory).where(PriceHistory.watch_id == watch.id)
        )
        history_entries = result.scalars().all()
        assert len(history_entries) == 1
        assert history_entries[0].price == new_price
        assert history_entries[0].buy_box_seller == seller

    async def test_update_watch_price_unchanged_still_creates_history(
        self, test_user, db_session
    ):
        """Test 8: update_watch_price still creates history even if price unchanged."""
        # Create a watch with a price
        initial_price = 100.0
        watch = await create_watch(
            user_id=str(test_user.id),
            asin="B08N5WRWNW",
            target_price=50.0,
            current_price=initial_price,
        )

        # Update with the same price
        updated_watch = await update_watch_price(
            str(watch.id),
            initial_price,
        )

        # Verify watch was still processed
        assert updated_watch is not None
        assert updated_watch.current_price == initial_price

        # Verify PriceHistory entry was still created
        result = await db_session.execute(
            select(PriceHistory).where(PriceHistory.watch_id == watch.id)
        )
        history_entries = result.scalars().all()
        assert len(history_entries) == 1
        assert history_entries[0].price == initial_price


class TestPriceAlerts:
    """Tests for price alert functions."""

    async def test_create_price_alert(self, test_user):
        """Test 9: create_price_alert creates alert row."""
        # Create a watch first
        watch = await create_watch(
            user_id=str(test_user.id),
            asin="B08N5WRWNW",
            target_price=100.0,
        )

        # Create an alert
        alert = await create_price_alert(
            watch_id=str(watch.id),
            triggered_price=90.0,
            target_price=100.0,
        )

        assert alert is not None
        assert alert.watch_id == watch.id
        assert alert.triggered_price == 90.0
        assert alert.target_price == 100.0
        assert alert.status == AlertStatus.PENDING
        assert isinstance(alert.id, uuid.UUID)

    async def test_get_pending_alerts_returns_unsent(self, test_user):
        """Test 10: get_pending_alerts returns unsent (pending) alerts."""
        # Create a watch and alerts
        watch = await create_watch(
            user_id=str(test_user.id),
            asin="B08N5WRWNW",
            target_price=100.0,
        )

        alert1 = await create_price_alert(
            watch_id=str(watch.id),
            triggered_price=90.0,
            target_price=100.0,
        )
        alert2 = await create_price_alert(
            watch_id=str(watch.id),
            triggered_price=85.0,
            target_price=100.0,
        )

        pending = await get_pending_alerts()

        assert len(pending) == 2
        alert_ids = {a.id for a in pending}
        assert alert1.id in alert_ids
        assert alert2.id in alert_ids

    async def test_get_pending_alerts_excludes_sent(self, test_user):
        """Test 11: get_pending_alerts excludes already-sent alerts."""
        # Create a watch and alerts
        watch = await create_watch(
            user_id=str(test_user.id),
            asin="B08N5WRWNW",
            target_price=100.0,
        )

        pending_alert = await create_price_alert(
            watch_id=str(watch.id),
            triggered_price=90.0,
            target_price=100.0,
        )
        sent_alert = await create_price_alert(
            watch_id=str(watch.id),
            triggered_price=85.0,
            target_price=100.0,
        )

        # Mark one alert as sent
        await mark_alert_sent(str(sent_alert.id))

        pending = await get_pending_alerts()

        assert len(pending) == 1
        assert pending[0].id == pending_alert.id

    async def test_mark_alert_sent(self, test_user):
        """Test 12: mark_alert_sent sets sent_at timestamp and status."""
        # Create a watch and alert
        watch = await create_watch(
            user_id=str(test_user.id),
            asin="B08N5WRWNW",
            target_price=100.0,
        )
        alert = await create_price_alert(
            watch_id=str(watch.id),
            triggered_price=90.0,
            target_price=100.0,
        )

        assert alert.status == AlertStatus.PENDING
        assert alert.sent_at is None

        # Mark as sent
        await mark_alert_sent(str(alert.id))

        # Verify alert was updated by fetching fresh from DB
        pending = await get_pending_alerts()
        assert len(pending) == 0  # Should not be in pending anymore


class TestSoftDeleteWatch:
    """Tests for soft_delete_watch function."""

    async def test_soft_delete_watch_sets_inactive(self, test_user):
        """Test 13: soft_delete_watch sets status to INACTIVE."""
        # Create a watch
        watch = await create_watch(
            user_id=str(test_user.id),
            asin="B08N5WRWNW",
            target_price=100.0,
        )
        assert watch.status == WatchStatus.ACTIVE

        # Soft delete
        result = await soft_delete_watch(str(watch.id), str(test_user.id))

        assert result is True

        # Verify watch no longer appears in active watches
        active_watches = await get_active_watches()
        watch_ids = {w.id for w in active_watches}
        assert watch.id not in watch_ids

        # Verify watch no longer appears in user watches
        user_watches = await get_user_watches(str(test_user.id))
        user_watch_ids = {w.id for w in user_watches}
        assert watch.id not in user_watch_ids


class TestGetUserById:
    """Tests for get_user_by_id function."""

    async def test_get_user_by_id_returns_correct_user(self, test_user):
        """Test 14: get_user_by_id returns correct user."""
        user = await get_user_by_id(str(test_user.id))

        assert user is not None
        assert user.id == test_user.id
        assert user.email == test_user.email

    async def test_get_user_by_id_returns_none_for_unknown(self):
        """Test 15: get_user_by_id returns None for unknown id."""
        random_id = str(uuid.uuid4())
        user = await get_user_by_id(random_id)

        assert user is None


class TestUUIDs:
    """Tests for UUID handling."""

    async def test_watch_id_is_valid_uuid(self, test_user):
        """Test 16: watch id is a valid UUID object."""
        watch = await create_watch(
            user_id=str(test_user.id),
            asin="B08N5WRWNW",
            target_price=100.0,
        )

        assert isinstance(watch.id, uuid.UUID)
        # Verify it's a valid UUID by converting to string and back
        uuid_str = str(watch.id)
        parsed = uuid.UUID(uuid_str)
        assert parsed == watch.id


class TestPriceHistory:
    """Tests for PriceHistory model and relationships."""

    async def test_price_history_has_watch_id_foreign_key(self, test_user, db_session):
        """Test 17: PriceHistory has watch_id foreign key referencing watch."""
        from sqlalchemy.orm import selectinload

        # Create a watch
        watch = await create_watch(
            user_id=str(test_user.id),
            asin="B08N5WRWNW",
            target_price=100.0,
        )

        # Update price to create history
        await update_watch_price(str(watch.id), 90.0)

        # Query price history with eager-loaded relationship
        result = await db_session.execute(
            select(PriceHistory)
            .where(PriceHistory.watch_id == watch.id)
            .options(selectinload(PriceHistory.watch))
        )
        history = result.scalar_one()

        assert history.watch_id == watch.id
        # Verify the relationship works with eager loading
        assert history.watch is not None
        assert history.watch.id == watch.id


class TestMultipleWatches:
    """Tests for multiple watches per user."""

    async def test_get_user_watches_returns_all_for_user(self, test_user, test_user2):
        """Test 18: get_user_watches returns all watches for that user only."""
        # Create watches for user 1
        watch1 = await create_watch(
            user_id=str(test_user.id),
            asin="B08N5WRWNW",
            target_price=100.0,
        )
        watch2 = await create_watch(
            user_id=str(test_user.id),
            asin="B08N5M7S6K",
            target_price=200.0,
        )

        # Create watch for user 2
        watch3 = await create_watch(
            user_id=str(test_user2.id),
            asin="B09V3KXJPB",
            target_price=300.0,
        )

        # Get watches for user 1
        user1_watches = await get_user_watches(str(test_user.id))
        assert len(user1_watches) == 2
        user1_ids = {w.id for w in user1_watches}
        assert watch1.id in user1_ids
        assert watch2.id in user1_ids
        assert watch3.id not in user1_ids

        # Get watches for user 2
        user2_watches = await get_user_watches(str(test_user2.id))
        assert len(user2_watches) == 1
        assert user2_watches[0].id == watch3.id


class TestGetActiveWatchCount:
    """Tests for get_active_watch_count function."""

    async def test_get_active_watch_count(self, test_user):
        """Test: get_active_watch_count returns correct count of active watches."""
        # Initially should be 0
        count = await get_active_watch_count()
        initial_count = count

        # Create watches
        await create_watch(
            user_id=str(test_user.id),
            asin="B08N5WRWNW",
            target_price=100.0,
        )
        await create_watch(
            user_id=str(test_user.id),
            asin="B08N5M7S6K",
            target_price=200.0,
        )

        count = await get_active_watch_count()
        assert count == initial_count + 2

        # Soft delete one
        watches = await get_user_watches(str(test_user.id))
        await soft_delete_watch(str(watches[0].id), str(test_user.id))

        count = await get_active_watch_count()
        assert count == initial_count + 1
