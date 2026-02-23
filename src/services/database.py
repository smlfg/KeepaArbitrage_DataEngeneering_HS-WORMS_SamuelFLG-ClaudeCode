"""
Database models for Keeper System
Uses SQLAlchemy with async support for PostgreSQL
"""

import uuid
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    DateTime,
    Boolean,
    ForeignKey,
    Text,
    Enum as SQLEnum,
    JSON,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.future import select
from src.config import get_settings

logger = logging.getLogger(__name__)


# Create async engine
settings = get_settings()
DATABASE_URL = settings.database_url

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()


class WatchStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    INACTIVE = "INACTIVE"


class AlertStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class User(Base):
    """User model for multi-tenant support"""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    telegram_chat_id = Column(String(50), nullable=True)
    discord_webhook = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    watches = relationship("WatchedProduct", back_populates="user")
    deal_filters = relationship("DealFilter", back_populates="user")


class WatchedProduct(Base):
    """Products that user wants to monitor for price drops"""

    __tablename__ = "watched_products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    asin = Column(String(10), nullable=False, index=True)
    product_name = Column(String(500), nullable=True)
    target_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=True)
    volatility_score = Column(Float, nullable=True, default=0.0)
    status = Column(SQLEnum(WatchStatus), default=WatchStatus.ACTIVE)
    last_checked_at = Column(DateTime, nullable=True)
    last_price_change = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="watches")
    alerts = relationship("PriceAlert", back_populates="watch")
    price_history = relationship("PriceHistory", back_populates="watch")

    __table_args__ = (
        Index("idx_watched_products_user_asin", "user_id", "asin", unique=True),
    )


class PriceHistory(Base):
    """Historical price data for watched products"""

    __tablename__ = "price_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    watch_id = Column(
        UUID(as_uuid=True),
        ForeignKey("watched_products.id"),
        nullable=False,
        index=True,
    )
    price = Column(Float, nullable=False)
    buy_box_seller = Column(String(100), nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, index=True)

    watch = relationship("WatchedProduct", back_populates="price_history")

    __table_args__ = (Index("idx_price_history_watch_time", "watch_id", "recorded_at"),)


class PriceAlert(Base):
    """Alerts triggered when price drops below target"""

    __tablename__ = "price_alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    watch_id = Column(
        UUID(as_uuid=True),
        ForeignKey("watched_products.id"),
        nullable=False,
        index=True,
    )
    triggered_price = Column(Float, nullable=False)
    target_price = Column(Float, nullable=False)
    old_price = Column(Float, nullable=True)
    new_price = Column(Float, nullable=True)
    discount_percent = Column(Float, nullable=True)
    status = Column(SQLEnum(AlertStatus), default=AlertStatus.PENDING)
    triggered_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)
    notification_channel = Column(String(20), default="email")

    watch = relationship("WatchedProduct", back_populates="alerts")


class DealFilter(Base):
    """User-defined filters for deal searches"""

    __tablename__ = "deal_filters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    name = Column(String(100), nullable=False)
    categories = Column(JSON, nullable=True)
    min_price = Column(Float, default=0)
    max_price = Column(Float, default=500)
    min_discount = Column(Integer, default=20)
    max_discount = Column(Integer, default=80)
    min_rating = Column(Float, default=4.0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="deal_filters")


class DealReport(Base):
    """Generated deal reports sent to users"""

    __tablename__ = "deal_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filter_id = Column(
        UUID(as_uuid=True), ForeignKey("deal_filters.id"), nullable=False
    )
    deals_data = Column(JSON, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)


class CollectedDeal(Base):
    """
    Raw deal data collected from Keepa API.
    Stored in PostgreSQL for historical analysis and finding bargains.
    """

    __tablename__ = "collected_deals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asin = Column(String(10), nullable=False, index=True)
    title = Column(String(500), nullable=True)
    current_price = Column(Float, nullable=False)
    original_price = Column(Float, nullable=True)
    discount_percent = Column(Float, nullable=True)
    rating = Column(Float, nullable=True)
    review_count = Column(Integer, nullable=True)
    sales_rank = Column(Integer, nullable=True)
    domain = Column(String(10), default="de")
    category = Column(String(100), nullable=True)
    url = Column(String(200), nullable=True)
    prime_eligible = Column(Boolean, default=False)
    deal_score = Column(Float, nullable=True)
    collected_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("idx_collected_deals_asin_collected", "asin", "collected_at"),
        Index("idx_collected_deals_discount", "discount_percent"),
        Index("idx_collected_deals_price", "current_price"),
    )


# Database operations
async def init_db():
    """Initialize database tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """Get database session"""
    async with async_session_maker() as session:
        yield session


async def create_watch(
    user_id: str, asin: str, target_price: float, current_price: float = None
) -> WatchedProduct:
    """Create a new watched product"""
    async with async_session_maker() as session:
        watch = WatchedProduct(
            user_id=uuid.UUID(user_id),
            asin=asin,
            target_price=target_price,
            current_price=current_price,
        )
        session.add(watch)
        await session.commit()
        await session.refresh(watch)
        return watch


async def get_user_watches(user_id: str) -> List[WatchedProduct]:
    """Get all watches for a user"""
    async with async_session_maker() as session:
        result = await session.execute(
            select(WatchedProduct).where(
                WatchedProduct.user_id == uuid.UUID(user_id),
                WatchedProduct.status == WatchStatus.ACTIVE,
            )
        )
        return result.scalars().all()


async def get_active_watches() -> List[WatchedProduct]:
    """Get all active watches for scheduled price checks"""
    async with async_session_maker() as session:
        result = await session.execute(
            select(WatchedProduct).where(WatchedProduct.status == WatchStatus.ACTIVE)
        )
        return result.scalars().all()


async def get_active_watch_count() -> int:
    """Count active watches (for health/status endpoints)"""
    async with async_session_maker() as session:
        result = await session.execute(
            select(WatchedProduct.id).where(WatchedProduct.status == WatchStatus.ACTIVE)
        )
        return len(result.scalars().all())


async def update_watch_price(
    watch_id: str, current_price: float, buy_box_seller: str = None
) -> WatchedProduct:
    """Update watch with current price and record history"""
    async with async_session_maker() as session:
        # Get the watch
        result = await session.execute(
            select(WatchedProduct).where(WatchedProduct.id == uuid.UUID(watch_id))
        )
        watch = result.scalar_one_or_none()

        if watch:
            # Record price history
            history = PriceHistory(
                watch_id=watch.id, price=current_price, buy_box_seller=buy_box_seller
            )
            session.add(history)

            # Update watch
            watch.current_price = current_price
            watch.last_checked_at = datetime.utcnow()
            watch.updated_at = datetime.utcnow()

            await session.commit()
            await session.refresh(watch)

        return watch


async def create_price_alert(
    watch_id: str, triggered_price: float, target_price: float
) -> PriceAlert:
    """Create a new price alert"""
    async with async_session_maker() as session:
        alert = PriceAlert(
            watch_id=uuid.UUID(watch_id),
            triggered_price=triggered_price,
            target_price=target_price,
        )
        session.add(alert)
        await session.commit()
        await session.refresh(alert)
        return alert


async def get_pending_alerts() -> List[PriceAlert]:
    """Get all pending alerts"""
    async with async_session_maker() as session:
        result = await session.execute(
            select(PriceAlert).where(PriceAlert.status == AlertStatus.PENDING)
        )
        return result.scalars().all()


async def mark_alert_sent(alert_id: str):
    """Mark alert as sent"""
    async with async_session_maker() as session:
        result = await session.execute(
            select(PriceAlert).where(PriceAlert.id == uuid.UUID(alert_id))
        )
        alert = result.scalar_one_or_none()
        if alert:
            alert.status = AlertStatus.SENT
            alert.sent_at = datetime.utcnow()
            await session.commit()


async def soft_delete_watch(watch_id: str, user_id: str) -> bool:
    """Soft delete a watch by marking it INACTIVE for a given user"""
    async with async_session_maker() as session:
        result = await session.execute(
            select(WatchedProduct).where(
                WatchedProduct.id == uuid.UUID(watch_id),
                WatchedProduct.user_id == uuid.UUID(user_id),
            )
        )
        watch = result.scalar_one_or_none()
        if not watch:
            return False

        watch.status = WatchStatus.INACTIVE
        watch.updated_at = datetime.utcnow()
        await session.commit()
        return True


async def get_user_by_id(user_id: str) -> Optional[User]:
    """Fetch a user by UUID"""
    async with async_session_maker() as session:
        result = await session.execute(
            select(User).where(User.id == uuid.UUID(user_id))
        )
        return result.scalar_one_or_none()


async def get_active_deal_filters_with_users() -> List[dict]:
    """Return active deal filters with their owning user for daily report dispatch."""
    async with async_session_maker() as session:
        stmt = (
            select(DealFilter, User)
            .join(User, DealFilter.user_id == User.id)
            .where(DealFilter.is_active == True)
        )
        result = await session.execute(stmt)
        rows = result.all()
        return [{"filter": f, "user": u} for f, u in rows]


async def get_pending_alerts_with_context() -> List[dict]:
    """
    Return pending alerts enriched with watch + user data so notification logic
    can pick appropriate channels.
    """
    async with async_session_maker() as session:
        stmt = (
            select(PriceAlert, WatchedProduct, User)
            .join(WatchedProduct, PriceAlert.watch_id == WatchedProduct.id)
            .join(User, WatchedProduct.user_id == User.id)
            .where(PriceAlert.status == AlertStatus.PENDING)
        )
        result = await session.execute(stmt)
        rows = result.all()

        enriched = []
        for alert, watch, user in rows:
            enriched.append(
                {
                    "alert": alert,
                    "watch": watch,
                    "user": user,
                }
            )
        return enriched


async def save_collected_deal(
    asin: str,
    title: str,
    current_price: float,
    original_price: float = None,
    discount_percent: float = None,
    rating: float = None,
    review_count: int = None,
    sales_rank: int = None,
    domain: str = "de",
    category: str = None,
    url: str = None,
    prime_eligible: bool = False,
    deal_score: float = None,
) -> CollectedDeal:
    """
    Save a collected deal to PostgreSQL for historical analysis.
    """
    async with async_session_maker() as session:
        deal = CollectedDeal(
            asin=asin,
            title=title,
            current_price=current_price,
            original_price=original_price,
            discount_percent=discount_percent,
            rating=rating,
            review_count=review_count,
            sales_rank=sales_rank,
            domain=domain,
            category=category,
            url=url,
            prime_eligible=prime_eligible,
            deal_score=deal_score,
        )
        session.add(deal)
        await session.commit()
        await session.refresh(deal)
        return deal


async def save_collected_deals_batch(deals: List[Dict]) -> int:
    """
    Save a batch of collected deals to PostgreSQL.
    Returns the number of deals saved.
    """
    saved = 0
    async with async_session_maker() as session:
        for deal_data in deals:
            try:
                deal = CollectedDeal(
                    asin=deal_data.get("asin", ""),
                    title=deal_data.get("title", ""),
                    current_price=deal_data.get("current_price", 0),
                    original_price=deal_data.get("original_price"),
                    discount_percent=deal_data.get("discount_percent"),
                    rating=deal_data.get("rating"),
                    review_count=deal_data.get("review_count"),
                    sales_rank=deal_data.get("sales_rank"),
                    domain=deal_data.get("domain", "de"),
                    category=deal_data.get("category"),
                    url=deal_data.get("url"),
                    prime_eligible=deal_data.get("prime_eligible", False),
                    deal_score=deal_data.get("deal_score"),
                )
                session.add(deal)
                saved += 1
            except Exception:
                pass
        await session.commit()
    return saved


async def get_latest_deal_price(asin: str) -> Optional[float]:
    """Get the most recent price for an ASIN from collected_deals table."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(CollectedDeal.current_price)
            .where(CollectedDeal.asin == asin, CollectedDeal.current_price > 0)
            .order_by(CollectedDeal.collected_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return float(row) if row else None


async def get_best_deals(
    min_discount: float = 30,
    min_rating: float = 4.0,
    max_price: float = 100,
    limit: int = 50,
) -> List[CollectedDeal]:
    """
    Get the best current deals from collected deals.
    """
    async with async_session_maker() as session:
        from sqlalchemy import and_

        result = await session.execute(
            select(CollectedDeal)
            .where(
                and_(
                    CollectedDeal.discount_percent >= min_discount,
                    CollectedDeal.rating >= min_rating,
                    CollectedDeal.current_price <= max_price,
                )
            )
            .order_by(CollectedDeal.discount_percent.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# System user for auto-tracked products
SYSTEM_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def get_or_create_system_user() -> User:
    """Get or create the system user for auto-tracked products."""
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.id == SYSTEM_USER_ID))
        user = result.scalars().first()
        if user:
            return user
        user = User(
            id=SYSTEM_USER_ID,
            email="system@keeper.internal",
            is_active=True,
        )
        session.add(user)
        try:
            await session.commit()
            await session.refresh(user)
        except Exception:
            await session.rollback()
            result = await session.execute(
                select(User).where(User.id == SYSTEM_USER_ID)
            )
            user = result.scalars().first()
        return user


async def ensure_tracked_product(
    asin: str, title: str = None, current_price: float = None, domain: str = "de"
) -> uuid.UUID:
    """Ensure an ASIN is tracked as a WatchedProduct. Returns watch_id."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(WatchedProduct).where(
                WatchedProduct.asin == asin,
                WatchedProduct.user_id == SYSTEM_USER_ID,
            )
        )
        watch = result.scalars().first()
        if watch:
            return watch.id

        await get_or_create_system_user()

        watch = WatchedProduct(
            user_id=SYSTEM_USER_ID,
            asin=asin,
            product_name=title,
            target_price=0.0,
            current_price=current_price,
            status=WatchStatus.ACTIVE,
        )
        session.add(watch)
        try:
            await session.commit()
            await session.refresh(watch)
            return watch.id
        except Exception:
            await session.rollback()
            result = await session.execute(
                select(WatchedProduct).where(
                    WatchedProduct.asin == asin,
                    WatchedProduct.user_id == SYSTEM_USER_ID,
                )
            )
            watch = result.scalars().first()
            return watch.id if watch else None


async def record_deal_price(
    asin: str,
    price: float,
    title: str = None,
    source: str = "deals_api",
    recorded_at: datetime = None,
) -> bool:
    """Record a price point from a deal snapshot into price_history."""
    if price <= 0:
        return False

    watch_id = await ensure_tracked_product(asin, title=title, current_price=price)
    if not watch_id:
        return False

    async with async_session_maker() as session:
        history = PriceHistory(
            watch_id=watch_id,
            price=price,
            buy_box_seller=source,
            recorded_at=recorded_at or datetime.utcnow(),
        )
        session.add(history)

        result = await session.execute(
            select(WatchedProduct).where(WatchedProduct.id == watch_id)
        )
        watch = result.scalars().first()
        if watch:
            watch.current_price = price
            watch.last_checked_at = datetime.utcnow()

        await session.commit()
        return True


async def backfill_price_history_from_deals() -> int:
    """Backfill price_history from existing collected_deals. Idempotent."""
    from sqlalchemy import func, distinct

    async with async_session_maker() as session:
        existing = await session.execute(
            select(func.count())
            .select_from(PriceHistory)
            .join(WatchedProduct, PriceHistory.watch_id == WatchedProduct.id)
            .where(WatchedProduct.user_id == SYSTEM_USER_ID)
        )
        if existing.scalar() > 0:
            logger.info("Backfill already done, skipping")
            return 0

    async with async_session_maker() as session:
        asins_result = await session.execute(
            select(distinct(CollectedDeal.asin)).where(CollectedDeal.current_price > 0)
        )
        asins = [row[0] for row in asins_result.all()]

    if not asins:
        logger.info("No collected deals to backfill")
        return 0

    total = 0
    for asin in asins:
        async with async_session_maker() as session:
            deals = await session.execute(
                select(CollectedDeal)
                .where(
                    CollectedDeal.asin == asin,
                    CollectedDeal.current_price > 0,
                )
                .order_by(CollectedDeal.collected_at)
            )
            for deal in deals.scalars().all():
                success = await record_deal_price(
                    asin=deal.asin,
                    price=deal.current_price,
                    title=deal.title,
                    source="backfill",
                    recorded_at=deal.collected_at,
                )
                if success:
                    total += 1

    logger.info(f"Backfilled {total} price history records from {len(asins)} ASINs")
    return total
