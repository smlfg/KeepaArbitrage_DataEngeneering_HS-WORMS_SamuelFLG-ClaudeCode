"""
Tests for pipeline_logger module

Covers: stage constants, setup_logger, log_api_call, log_parser,
log_filter, log_es_index, log_kafka_produce, log_kafka_consume, log_arbitrage
"""

import pytest
from unittest.mock import patch, MagicMock

from src.utils.pipeline_logger import (
    KEEPA_API,
    PARSER,
    FILTER,
    ES_INDEX,
    KAFKA_PRODUCER,
    KAFKA_CONSUMER,
    ARBITRAGE,
    PipelineStage,
    setup_logger,
    log_api_call,
    log_parser,
    log_filter,
    log_es_index,
    log_kafka_produce,
    log_kafka_consume,
    log_arbitrage,
)


# =============================================================================
# Stage Constants
# =============================================================================


class TestStageConstants:
    def test_keepa_api_constant(self):
        assert KEEPA_API == "keepa_api"

    def test_parser_constant(self):
        assert PARSER == "parser"

    def test_filter_constant(self):
        assert FILTER == "filter"

    def test_es_index_constant(self):
        assert ES_INDEX == "es_index"

    def test_kafka_producer_constant(self):
        assert KAFKA_PRODUCER == "kafka_producer"

    def test_kafka_consumer_constant(self):
        assert KAFKA_CONSUMER == "kafka_consumer"

    def test_arbitrage_constant(self):
        assert ARBITRAGE == "arbitrage"


# =============================================================================
# setup_logger
# =============================================================================


class TestSetupLogger:
    def test_returns_bound_logger(self):
        logger = setup_logger()
        assert logger is not None


# =============================================================================
# Log Functions
# =============================================================================


class TestLogApiCall:
    @patch("src.utils.pipeline_logger._log")
    def test_logs_api_call(self, mock_log):
        log_api_call(
            asins=["B123", "B456"],
            domain="DE",
            tokens_consumed=10,
            response_time_ms=250.0,
        )

        mock_log.info.assert_called_once()
        call_kwargs = mock_log.info.call_args.kwargs
        assert call_kwargs["stage"] == KEEPA_API
        assert call_kwargs["success"] is True
        assert call_kwargs["domain"] == "DE"
        assert call_kwargs["input"]["count"] == 2
        assert call_kwargs["output"]["tokens_consumed"] == 10


class TestLogParser:
    @patch("src.utils.pipeline_logger._log")
    def test_logs_parser_success(self, mock_log):
        log_parser(
            asin="B123",
            extracted_fields={"title": "Keyboard", "price": 49.99},
            missing_fields=[],
        )

        call_kwargs = mock_log.info.call_args.kwargs
        assert call_kwargs["stage"] == PARSER
        assert call_kwargs["success"] is True
        assert call_kwargs["asin"] == "B123"

    @patch("src.utils.pipeline_logger._log")
    def test_logs_parser_with_missing_fields(self, mock_log):
        log_parser(
            asin="B456",
            extracted_fields={"title": "Keyboard"},
            missing_fields=["price", "rating"],
        )

        call_kwargs = mock_log.info.call_args.kwargs
        assert call_kwargs["success"] is False
        assert call_kwargs["output"]["missing_fields"] == ["price", "rating"]


class TestLogFilter:
    @patch("src.utils.pipeline_logger._log")
    def test_logs_filter_in(self, mock_log):
        log_filter(asin="B123", filtered_in=True)

        call_kwargs = mock_log.info.call_args.kwargs
        assert call_kwargs["stage"] == FILTER
        assert call_kwargs["success"] is True

    @patch("src.utils.pipeline_logger._log")
    def test_logs_filter_out_with_reason(self, mock_log):
        log_filter(asin="B123", filtered_in=False, reason="not a keyboard")

        call_kwargs = mock_log.info.call_args.kwargs
        assert call_kwargs["success"] is False
        assert call_kwargs["output"]["reason"] == "not a keyboard"


class TestLogEsIndex:
    @patch("src.utils.pipeline_logger._log")
    def test_logs_es_index_success(self, mock_log):
        log_es_index(docs_indexed=5)

        call_kwargs = mock_log.info.call_args.kwargs
        assert call_kwargs["stage"] == ES_INDEX
        assert call_kwargs["success"] is True
        assert call_kwargs["input"]["docs_indexed"] == 5

    @patch("src.utils.pipeline_logger._log")
    def test_logs_es_index_with_errors(self, mock_log):
        log_es_index(docs_indexed=0, errors=["timeout"])

        call_kwargs = mock_log.info.call_args.kwargs
        assert call_kwargs["success"] is False
        assert call_kwargs["output"]["errors"] == ["timeout"]


class TestLogKafkaProduce:
    @patch("src.utils.pipeline_logger._log")
    def test_logs_kafka_produce(self, mock_log):
        log_kafka_produce(topic="price-updates", partition=0, offset=42)

        call_kwargs = mock_log.info.call_args.kwargs
        assert call_kwargs["stage"] == KAFKA_PRODUCER
        assert call_kwargs["success"] is True
        assert call_kwargs["output"]["partition"] == 0
        assert call_kwargs["output"]["offset"] == 42


class TestLogKafkaConsume:
    @patch("src.utils.pipeline_logger._log")
    def test_logs_kafka_consume(self, mock_log):
        log_kafka_consume(messages_processed=100, duration_ms=5000.0)

        call_kwargs = mock_log.info.call_args.kwargs
        assert call_kwargs["stage"] == KAFKA_CONSUMER
        assert call_kwargs["success"] is True
        assert call_kwargs["input"]["messages_processed"] == 100
        assert call_kwargs["duration_ms"] == 5000.0


class TestLogArbitrage:
    @patch("src.utils.pipeline_logger._log")
    def test_logs_arbitrage_with_opportunities(self, mock_log):
        log_arbitrage(opportunities_found=3, margin_eur=45.50)

        call_kwargs = mock_log.info.call_args.kwargs
        assert call_kwargs["stage"] == ARBITRAGE
        assert call_kwargs["success"] is True
        assert call_kwargs["output"]["opportunities_found"] == 3
        assert call_kwargs["output"]["margin_eur"] == 45.50

    @patch("src.utils.pipeline_logger._log")
    def test_logs_arbitrage_no_opportunities(self, mock_log):
        log_arbitrage(opportunities_found=0)

        call_kwargs = mock_log.info.call_args.kwargs
        assert call_kwargs["success"] is False

    @patch("src.utils.pipeline_logger._log")
    def test_logs_arbitrage_with_top_margin_eur_alias(self, mock_log):
        log_arbitrage(opportunities_found=3, top_margin_eur=42.50)

        call_kwargs = mock_log.info.call_args.kwargs
        assert call_kwargs["output"]["margin_eur"] == 42.50
        assert call_kwargs["output"]["opportunities_found"] == 3

    @patch("src.utils.pipeline_logger._log")
    def test_logs_arbitrage_margin_eur_takes_precedence(self, mock_log):
        log_arbitrage(opportunities_found=1, margin_eur=10.0, top_margin_eur=99.0)

        call_kwargs = mock_log.info.call_args.kwargs
        assert call_kwargs["output"]["margin_eur"] == 10.0


# =============================================================================
# PipelineStage backward-compatibility class
# =============================================================================


class TestPipelineStage:
    def test_has_core_stage_attributes(self):
        assert PipelineStage.KEEPA_API == KEEPA_API
        assert PipelineStage.PARSER == PARSER
        assert PipelineStage.FILTER == FILTER
        assert PipelineStage.ES_INDEX == ES_INDEX
        assert PipelineStage.KAFKA_PRODUCER == KAFKA_PRODUCER
        assert PipelineStage.KAFKA_CONSUMER == KAFKA_CONSUMER
        assert PipelineStage.ARBITRAGE == ARBITRAGE

    def test_has_extract_and_load(self):
        assert hasattr(PipelineStage, "EXTRACT")
        assert hasattr(PipelineStage, "LOAD")
        assert PipelineStage.EXTRACT == "extract"
        assert PipelineStage.LOAD == "load"
