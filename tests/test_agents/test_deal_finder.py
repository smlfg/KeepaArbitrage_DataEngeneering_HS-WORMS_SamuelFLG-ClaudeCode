import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.agents.deal_finder import DealFinderAgent, deal_finder


@pytest.fixture
def agent():
    return DealFinderAgent()


@pytest.fixture
def sample_deal():
    return {
        "asin": "B08N5WRWNW",
        "title": "Test Product",
        "current_price": 100.0,
        "list_price": 150.0,
        "discount_percent": 33.3,
        "rating": 4.5,
        "reviews": 120,
        "sales_rank": 5000,
        "url": "https://amazon.de/dp/B08N5WRWNW",
        "source": "product_api",
    }


@pytest.fixture
def sample_filter_config():
    return {
        "seed_asins": ["B08N5WRWNW", "B09G9FPHY6"],
        "min_discount": 20,
        "max_discount": 80,
        "min_price": 50,
        "max_price": 500,
        "min_rating": 4.0,
    }


class TestDealFinderInit:
    def test_init_default_settings(self, agent):
        assert agent.MIN_RATING == 3.5
        assert agent.MIN_DEALS_FOR_REPORT == 5
        assert agent.MAX_DEALS_PER_REPORT == 15
        assert agent.MIN_PRICE_THRESHOLD == 10.0


class TestSeedSource:
    def test_get_seed_asins_uses_file_when_env_empty(self, agent, tmp_path):
        seed_file = tmp_path / "seed_asins.txt"
        seed_file.write_text("B08N5WRWNW\nB09G9FPHY6", encoding="utf-8")

        agent.settings.deal_seed_asins = ""
        agent.settings.deal_seed_file = str(seed_file)

        result = agent._get_seed_asins({})
        assert "B08N5WRWNW" in result
        assert "B09G9FPHY6" in result

    def test_select_candidate_asins_rotates_with_offset(self, agent):
        seeds = ["B000000001", "B000000002", "B000000003", "B000000004"]

        first_batch = agent._select_candidate_asins(seeds, max_asins=2, start_offset=0)
        wrapped_batch = agent._select_candidate_asins(seeds, max_asins=2, start_offset=3)

        assert first_batch == ["B000000001", "B000000002"]
        assert wrapped_batch == ["B000000004", "B000000001"]


class TestSearchDeals:
    @pytest.mark.asyncio
    async def test_search_deals_calls_deals_api_and_limits_results(self, agent):
        filter_config = {
            "max_asins": 2,
            "min_discount": 0,
            "max_discount": 100,
            "min_price": 0,
            "max_price": 1000,
            "min_rating": 0,
        }

        with patch("src.agents.deal_finder.get_keepa_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.search_deals = AsyncMock(return_value={
                "deals": [
                    {"asin": "B000000004", "title": "Product 4", "current_price": 50.0, "list_price": 100.0, "discount_percent": 50, "rating": 4.2, "reviews": 30},
                    {"asin": "B000000001", "title": "Product 1", "current_price": 60.0, "list_price": 100.0, "discount_percent": 40, "rating": 4.3, "reviews": 25},
                    {"asin": "B000000002", "title": "Product 2", "current_price": 70.0, "list_price": 100.0, "discount_percent": 30, "rating": 4.1, "reviews": 20},
                ],
                "total": 3,
                "page": 0,
                "category_names": [],
            })
            mock_get_client.return_value = mock_client

            result = await agent.search_deals(filter_config)

            mock_client.search_deals.assert_called_once()
            assert len(result) <= 2

    @pytest.mark.asyncio
    async def test_search_deals_returns_list_of_scored_deals(
        self, agent, sample_filter_config
    ):
        with patch("src.agents.deal_finder.get_keepa_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.search_deals = AsyncMock(return_value={
                "deals": [
                    {"asin": "B08N5WRWNW", "title": "Product A", "current_price": 99.0, "list_price": 150.0, "discount_percent": 34, "rating": 4.6, "reviews": 200},
                    {"asin": "B09G9FPHY6", "title": "Product B", "current_price": 120.0, "list_price": 220.0, "discount_percent": 45, "rating": 4.2, "reviews": 100},
                ],
                "total": 2,
                "page": 0,
                "category_names": [],
            })
            mock_get_client.return_value = mock_client

            result = await agent.search_deals(sample_filter_config)

            assert isinstance(result, list)
            assert len(result) == 2
            assert all("deal_score" in deal for deal in result)
            assert all("current_price" in deal for deal in result)

    @pytest.mark.asyncio
    async def test_search_deals_empty_response_returns_empty_list(
        self, agent, sample_filter_config
    ):
        with patch("src.agents.deal_finder.get_keepa_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.search_deals = AsyncMock(return_value={
                "deals": [],
                "total": 0,
                "page": 0,
                "category_names": [],
            })
            mock_get_client.return_value = mock_client

            result = await agent.search_deals(sample_filter_config)
            assert result == []

    @pytest.mark.asyncio
    async def test_search_deals_handles_api_error(self, agent, sample_filter_config):
        with patch("src.agents.deal_finder.get_keepa_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.search_deals = AsyncMock(
                side_effect=Exception("API Error")
            )
            mock_get_client.return_value = mock_client

            result = await agent.search_deals(sample_filter_config)
            assert result == []

    @pytest.mark.asyncio
    async def test_search_deals_respects_max_per_report(self, agent):
        filter_config = {
            "min_discount": 0,
            "max_discount": 100,
            "min_price": 0,
            "max_price": 1000,
            "min_rating": 0,
        }

        deals_data = [
            {"asin": f"B{i:09d}", "title": f"Product {i}", "current_price": 100, "list_price": 200, "discount_percent": 50, "rating": 4.0, "reviews": 10}
            for i in range(30)
        ]

        with patch("src.agents.deal_finder.get_keepa_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.search_deals = AsyncMock(return_value={
                "deals": deals_data,
                "total": 30,
                "page": 0,
                "category_names": [],
            })
            mock_get_client.return_value = mock_client

            result = await agent.search_deals(filter_config)

            assert len(result) <= agent.MAX_DEALS_PER_REPORT


class TestScoreDeal:
    def test_score_deal_higher_discount_higher_score(self, agent):
        deal1 = {
            "discount_percent": 50,
            "rating": 4.0,
            "sales_rank": 1000,
            "current_price": 100,
        }
        deal2 = {
            "discount_percent": 20,
            "rating": 4.0,
            "sales_rank": 1000,
            "current_price": 100,
        }

        scored1 = agent._score_deal(deal1)
        scored2 = agent._score_deal(deal2)

        assert scored1["deal_score"] > scored2["deal_score"]

    def test_score_deal_reviews_rating_boost_score(self, agent):
        deal1 = {
            "discount_percent": 30,
            "rating": 5.0,
            "sales_rank": 1000,
            "current_price": 100,
        }
        deal2 = {
            "discount_percent": 30,
            "rating": 3.0,
            "sales_rank": 1000,
            "current_price": 100,
        }

        scored1 = agent._score_deal(deal1)
        scored2 = agent._score_deal(deal2)

        assert scored1["deal_score"] > scored2["deal_score"]

    def test_score_deal_includes_all_components(self, agent, sample_deal):
        result = agent._score_deal(sample_deal)

        assert "deal_score" in result
        assert 0 <= result["deal_score"] <= 100


class TestFilterSpam:
    def test_filter_spam_removes_low_rating(self, agent):
        deals = [
            {
                "rating": 2.0,
                "current_price": 50,
                "title": "Bad Product",
                "discount_percent": 30,
            },
            {
                "rating": 4.5,
                "current_price": 50,
                "title": "Good Product",
                "discount_percent": 30,
            },
        ]

        result = agent.filter_spam(deals)
        assert len(result) == 1
        assert result[0]["rating"] == 4.5

    def test_filter_spam_removes_low_price(self, agent):
        deals = [
            {
                "rating": 4.5,
                "current_price": 5,
                "title": "Cheap Product",
                "discount_percent": 30,
            },
            {
                "rating": 4.5,
                "current_price": 50,
                "title": "Good Product",
                "discount_percent": 30,
            },
        ]

        result = agent.filter_spam(deals)
        assert len(result) == 1
        assert result[0]["current_price"] == 50

    def test_filter_spam_removes_dropshipper_keywords(self, agent):
        deals = [
            {
                "rating": 4.5,
                "current_price": 50,
                "title": "Dropship Product",
                "discount_percent": 30,
            },
            {
                "rating": 4.5,
                "current_price": 50,
                "title": "Good Product",
                "discount_percent": 30,
            },
        ]

        result = agent.filter_spam(deals)
        assert len(result) == 1
        assert "Good" in result[0]["title"]


class TestShouldSendReport:
    def test_should_send_report_true_with_enough_deals(self, agent):
        deals = [
            {
                "rating": 4.5,
                "current_price": 50,
                "title": "Product",
                "discount_percent": 30,
            }
            for _ in range(10)
        ]
        assert agent.should_send_report(deals) is True

    def test_should_send_report_false_with_few_deals(self, agent):
        deals = [
            {
                "rating": 4.5,
                "current_price": 50,
                "title": "Product",
                "discount_percent": 30,
            }
            for _ in range(3)
        ]
        assert agent.should_send_report(deals) is False


class TestGenerateReport:
    @pytest.mark.asyncio
    async def test_generate_report_returns_html(self, agent):
        deals = [
            {
                "rating": 4.5,
                "current_price": 50,
                "title": "Product",
                "discount_percent": 30,
            }
        ]

        with patch("src.agents.deal_finder.notification_service") as mock_service:
            mock_service.format_deal_report_html = MagicMock(
                return_value="<html>Report</html>"
            )
            result = await agent.generate_report(deals, "Test Filter", "Summary")
            assert result == "<html>Report</html>"
            mock_service.format_deal_report_html.assert_called_once()


class TestRunDailySearch:
    @pytest.mark.asyncio
    async def test_run_daily_search_processes_filters(
        self, agent, sample_filter_config
    ):
        with (
            patch.object(agent, "search_deals", new_callable=AsyncMock) as mock_search,
            patch.object(
                agent, "_index_deal_to_elasticsearch", new_callable=AsyncMock
            ) as mock_index,
            patch("src.agents.deal_finder.notification_service") as mock_service,
        ):
            mock_search.return_value = []
            mock_index.return_value = True
            mock_service.format_deal_report_html = MagicMock(
                return_value="<html>Report</html>"
            )

            filters = [sample_filter_config, sample_filter_config]
            result = await agent.run_daily_search(filters)

            assert result["filters_processed"] == 2
            assert "results" in result


class TestDealSorting:
    def test_deals_sorted_by_score_descending(self, agent):
        deals = [
            {
                "asin": "B001",
                "discount_percent": 20,
                "rating": 4.0,
                "sales_rank": 1000,
                "current_price": 100,
            },
            {
                "asin": "B002",
                "discount_percent": 50,
                "rating": 4.0,
                "sales_rank": 1000,
                "current_price": 100,
            },
            {
                "asin": "B003",
                "discount_percent": 30,
                "rating": 4.0,
                "sales_rank": 1000,
                "current_price": 100,
            },
        ]

        scored = [agent._score_deal(deal) for deal in deals]
        scored.sort(key=lambda x: x["deal_score"], reverse=True)

        assert scored[0]["discount_percent"] == 50
        assert scored[1]["discount_percent"] == 30
        assert scored[2]["discount_percent"] == 20


class TestNormalizeDeal:
    def test_normalize_camel_case_to_snake_case(self, agent):
        """G2: Verify camelCase input is normalized to snake_case output."""
        camel_deal = {
            "asin": "B08TEST001",
            "title": "CamelCase Product",
            "currentPrice": 79.99,
            "listPrice": 129.99,
            "discountPercent": 38.5,
            "rating": 4.3,
            "reviewCount": 250,
            "salesRank": 3000,
            "amazonUrl": "https://amazon.de/dp/B08TEST001",
            "source": "product_api",
        }

        result = agent._normalize_deal(camel_deal)

        assert result["current_price"] == 79.99
        assert result["list_price"] == 129.99
        assert result["discount_percent"] == 38.5
        assert result["reviews"] == 250
        assert result["sales_rank"] == 3000
        assert result["url"] == "https://amazon.de/dp/B08TEST001"
        assert "currentPrice" not in result
        assert "listPrice" not in result

    def test_normalize_snake_case_passthrough(self, agent):
        """G2: Verify snake_case input passes through correctly."""
        snake_deal = {
            "asin": "B08TEST002",
            "title": "SnakeCase Product",
            "current_price": 49.99,
            "list_price": 99.99,
            "discount_percent": 50.0,
            "rating": 4.7,
            "reviews": 500,
            "sales_rank": 1000,
            "url": "https://amazon.de/dp/B08TEST002",
            "source": "product_api",
        }

        result = agent._normalize_deal(snake_deal)

        assert result["current_price"] == 49.99
        assert result["list_price"] == 99.99
        assert result["discount_percent"] == 50.0
        assert result["reviews"] == 500

    def test_normalize_computes_discount_when_missing(self, agent):
        """G2: discount_percent is calculated from prices when not provided."""
        deal = {
            "asin": "B08TEST003",
            "current_price": 75.0,
            "list_price": 100.0,
        }

        result = agent._normalize_deal(deal)

        assert result["discount_percent"] == 25.0


class TestDealFinderGlobal:
    def test_deal_finder_instance_exists(self):
        assert deal_finder is not None
        assert isinstance(deal_finder, DealFinderAgent)
