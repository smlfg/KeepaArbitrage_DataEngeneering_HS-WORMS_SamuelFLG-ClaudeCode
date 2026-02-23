"""
Pipeline Logging System for Keepa Project
Structured JSON logging for pipeline stages
"""

import structlog
from datetime import datetime, timezone
from typing import Any

KEEPA_API = "keepa_api"
PARSER = "parser"
FILTER = "filter"
ES_INDEX = "es_index"
KAFKA_PRODUCER = "kafka_producer"
KAFKA_CONSUMER = "kafka_consumer"
ARBITRAGE = "arbitrage"


class PipelineStage:
    """Backward-compatible stage enum for callers that use PipelineStage.X syntax."""
    KEEPA_API = KEEPA_API
    PARSER = PARSER
    FILTER = FILTER
    ES_INDEX = ES_INDEX
    KAFKA_PRODUCER = KAFKA_PRODUCER
    KAFKA_CONSUMER = KAFKA_CONSUMER
    ARBITRAGE = ARBITRAGE
    EXTRACT = "extract"
    LOAD = "load"


def setup_logger() -> structlog.BoundLogger:
    """
    Configure structlog for structured JSON logging.
    Outputs to stdout for docker/systemd capture.
    """
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    return structlog.get_logger()


_log = setup_logger()


def _log_event(
    stage: str,
    asin: str | None = None,
    domain: str | None = None,
    input: Any = None,
    output: Any = None,
    success: bool = True,
    duration_ms: float | None = None,
    **extra: Any,
) -> None:
    """Base logging function for pipeline events."""
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "success": success,
    }
    if asin is not None:
        event["asin"] = asin
    if domain is not None:
        event["domain"] = domain
    if input is not None:
        event["input"] = input
    if output is not None:
        event["output"] = output
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    event.update(extra)
    _log.info("pipeline_event", **event)


def log_api_call(
    asins: list[str],
    domain: str,
    tokens_consumed: int,
    response_time_ms: float,
) -> None:
    """Log Keepa API call."""
    _log_event(
        stage=KEEPA_API,
        input={"asins": asins, "count": len(asins)},
        output={"tokens_consumed": tokens_consumed},
        success=True,
        duration_ms=response_time_ms,
        domain=domain,
    )


def log_parser(
    asin: str,
    extracted_fields: dict[str, Any],
    missing_fields: list[str],
) -> None:
    """Log parser extraction results."""
    _log_event(
        stage=PARSER,
        asin=asin,
        input={"extracted_fields": list(extracted_fields.keys())},
        output={"missing_fields": missing_fields},
        success=len(missing_fields) == 0,
    )


def log_filter(
    asin: str,
    filtered_in: bool,
    reason: str | None = None,
) -> None:
    """Log filter stage results."""
    _log_event(
        stage=FILTER,
        asin=asin,
        output={"filtered_in": filtered_in, "reason": reason},
        success=filtered_in,
    )


def log_es_index(
    docs_indexed: int,
    errors: list[str] | None = None,
) -> None:
    """Log Elasticsearch index operations."""
    _log_event(
        stage=ES_INDEX,
        input={"docs_indexed": docs_indexed},
        output={"errors": errors} if errors else None,
        success=errors is None or len(errors) == 0,
    )


def log_kafka_produce(
    topic: str,
    partition: int,
    offset: int,
) -> None:
    """Log Kafka produce operations."""
    _log_event(
        stage=KAFKA_PRODUCER,
        input={"topic": topic},
        output={"partition": partition, "offset": offset},
        success=True,
    )


def log_kafka_consume(
    messages_processed: int,
    duration_ms: float,
) -> None:
    """Log Kafka consume operations."""
    _log_event(
        stage=KAFKA_CONSUMER,
        input={"messages_processed": messages_processed},
        duration_ms=duration_ms,
        success=True,
    )


def log_arbitrage(
    opportunities_found: int,
    margin_eur: float | None = None,
    top_margin_eur: float | None = None,
) -> None:
    """Log arbitrage analysis results."""
    effective_margin = margin_eur if margin_eur is not None else top_margin_eur
    _log_event(
        stage=ARBITRAGE,
        output={"opportunities_found": opportunities_found, "margin_eur": effective_margin},
        success=opportunities_found > 0,
    )


__all__ = [
    "KEEPA_API",
    "PARSER",
    "FILTER",
    "ES_INDEX",
    "KAFKA_PRODUCER",
    "KAFKA_CONSUMER",
    "ARBITRAGE",
    "PipelineStage",
    "setup_logger",
    "log_api_call",
    "log_parser",
    "log_filter",
    "log_es_index",
    "log_kafka_produce",
    "log_kafka_consume",
    "log_arbitrage",
]
