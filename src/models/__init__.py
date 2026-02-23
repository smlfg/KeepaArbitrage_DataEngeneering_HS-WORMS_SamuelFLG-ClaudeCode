# Models module exports — re-export from the canonical source
from src.services.database import (
    User,
    WatchedProduct,
    PriceHistory,
    PriceAlert,
    DealFilter,
    DealReport,
    WatchStatus,
    AlertStatus,
    Base,
    engine,
    async_session_maker,
    init_db,
    get_db,
)
