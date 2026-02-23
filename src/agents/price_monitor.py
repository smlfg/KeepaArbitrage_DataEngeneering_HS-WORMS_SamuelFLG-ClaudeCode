import asyncio

from src.services.keepa_api import get_keepa_client
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta


class PriceMonitorAgent:
    BATCH_SIZE = 50
    VOLATILE_THRESHOLD = 5.0
    STABLE_THRESHOLD = 2.0

    async def fetch_prices(self, asins: List[str]) -> List[Dict[str, Any]]:
        semaphore = asyncio.Semaphore(5)

        async def _fetch(asin: str):
            async with semaphore:
                return await get_keepa_client().query_product(asin)

        raw = await asyncio.gather(*[_fetch(a) for a in asins], return_exceptions=True)
        return [r for r in raw if isinstance(r, dict) and r]

    def calculate_volatility(
        self, current_price: float, last_price: Optional[float]
    ) -> float:
        if not last_price or last_price == 0:
            return 0.0
        return abs(current_price - last_price) / last_price * 100

    def determine_next_check_interval(self, volatility_score: float) -> timedelta:
        if volatility_score > self.VOLATILE_THRESHOLD:
            return timedelta(hours=2)
        elif volatility_score > self.STABLE_THRESHOLD:
            return timedelta(hours=4)
        else:
            return timedelta(hours=6)

    async def check_prices(self, watches: List[Dict[str, Any]]) -> Dict[str, Any]:
        alerts = []
        processed = 0
        errors = 0
        semaphore = asyncio.Semaphore(5)

        async def _check(watch: Dict[str, Any]):
            async with semaphore:
                asin = watch["asin"]
                result = await get_keepa_client().query_product(asin)
                return asin, watch["target_price"], result

        raw = await asyncio.gather(
            *[_check(w) for w in watches], return_exceptions=True
        )

        for item in raw:
            if isinstance(item, Exception):
                errors += 1
                continue
            asin, target_price, result = item
            if result:
                current_price = result.get("current_price", 0)
                if current_price <= target_price * 1.01:
                    alerts.append(
                        {
                            "asin": asin,
                            "current_price": current_price,
                            "target_price": target_price,
                            "alert_triggered": True,
                        }
                    )
                processed += 1
            else:
                errors += 1

        return {
            "processed": processed,
            "alerts_triggered": len(alerts),
            "errors": errors,
            "alerts": alerts,
        }

    async def batch_check(self, all_watches: List[Dict[str, Any]]) -> Dict[str, Any]:
        all_alerts = []
        total_processed = 0
        total_errors = 0

        for i in range(0, len(all_watches), self.BATCH_SIZE):
            batch = all_watches[i : i + self.BATCH_SIZE]
            result = await self.check_prices(batch)
            all_alerts.extend(result["alerts"])
            total_processed += result["processed"]
            total_errors += result["errors"]

        return {
            "processed": total_processed,
            "alerts": all_alerts,
            "errors": total_errors,
            "batches": (len(all_watches) + self.BATCH_SIZE - 1) // self.BATCH_SIZE,
        }


price_monitor = PriceMonitorAgent()
