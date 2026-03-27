"""
KafkaService — Event streaming layer for TruthLens AI.

Publishes detection results and feedback to Kafka topics.
If Kafka is unavailable, falls back to a local JSON event log
so the app never crashes.
"""

import json
import os
import datetime
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────
TOPIC_DETECTIONS = "deepfake-detections"
TOPIC_FEEDBACK = "deepfake-feedback"
EVENT_LOG_FILE = "event_log.json"


def _load_json_log() -> list:
    """Load the local JSON event log."""
    if os.path.exists(EVENT_LOG_FILE):
        try:
            with open(EVENT_LOG_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def _save_json_log(events: list):
    """Save events to the local JSON event log."""
    with open(EVENT_LOG_FILE, "w") as f:
        json.dump(events, f, indent=4, default=str)


class KafkaService:
    """
    Wraps kafka-python producer/consumer with graceful fallback.

    Usage:
        ks = KafkaService(broker="localhost:9092", enabled=True)
        ks.publish_detection({...})
        ks.publish_feedback({...})
        events = ks.get_recent_events(limit=50)
    """

    def __init__(self, broker: str = "localhost:9092", enabled: bool = True):
        self.broker = broker
        self.enabled = enabled
        self.producer = None
        self.connected = False

        if self.enabled:
            self._connect()

    # ── Connection ───────────────────────────────────────────
    def _connect(self):
        """Try to connect to Kafka broker. Fail silently if unavailable."""
        try:
            from kafka import KafkaProducer
            self.producer = KafkaProducer(
                bootstrap_servers=self.broker,
                value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
                request_timeout_ms=3000,
                max_block_ms=3000,
            )
            self.connected = True
            logger.info("✅ Kafka connected at %s", self.broker)
        except Exception as e:
            self.connected = False
            self.producer = None
            logger.warning(
                "⚠️  Kafka unavailable (%s). Falling back to JSON log. Error: %s",
                self.broker, e
            )

    # ── Publishing ───────────────────────────────────────────
    def _publish(self, topic: str, event: dict):
        """Publish an event to Kafka or fallback to JSON."""
        event["timestamp"] = datetime.datetime.now().isoformat()
        event["topic"] = topic

        if self.connected and self.producer:
            try:
                self.producer.send(topic, event)
                self.producer.flush(timeout=2)
                logger.info("📤 Kafka event sent → %s", topic)
            except Exception as e:
                logger.warning("Kafka send failed, using JSON fallback: %s", e)
                self._log_to_json(event)
        else:
            self._log_to_json(event)

    def _log_to_json(self, event: dict):
        """Append event to local JSON file."""
        events = _load_json_log()
        events.append(event)
        _save_json_log(events)
        logger.info("📝 Event saved to JSON log")

    def publish_detection(self, data: dict):
        """
        Publish a detection event.
        Expected data keys: filename, result, confidence
        """
        event = {
            "type": "detection",
            "filename": data.get("filename"),
            "result": data.get("result"),
            "confidence": data.get("confidence"),
        }
        self._publish(TOPIC_DETECTIONS, event)

    def publish_feedback(self, data: dict):
        """
        Publish a feedback event.
        Expected data keys: name, rating, message, was_prediction_correct
        """
        event = {
            "type": "feedback",
            "name": data.get("name"),
            "rating": data.get("rating"),
            "message": data.get("message"),
            "was_prediction_correct": data.get("was_prediction_correct"),
        }
        self._publish(TOPIC_FEEDBACK, event)

    # ── Consuming / Reading ──────────────────────────────────
    def get_recent_events(self, limit: int = 50, event_type: str = None) -> list:
        """
        Retrieve recent events. 
        If Kafka is connected, tries to consume; otherwise reads from JSON log.
        """
        events = []

        # Always include JSON log events (they're the fallback)
        json_events = _load_json_log()
        events.extend(json_events)

        # If Kafka connected, also try to consume recent messages
        if self.connected:
            try:
                from kafka import KafkaConsumer, TopicPartition
                consumer = KafkaConsumer(
                    bootstrap_servers=self.broker,
                    value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                    auto_offset_reset="earliest",
                    consumer_timeout_ms=3000,
                    group_id=None,  # No consumer group — read all
                )

                topics = [TOPIC_DETECTIONS, TOPIC_FEEDBACK]
                available_topics = consumer.topics()

                for topic in topics:
                    if topic in available_topics:
                        partitions = consumer.partitions_for_topic(topic)
                        if partitions:
                            for p in partitions:
                                tp = TopicPartition(topic, p)
                                consumer.assign([tp])
                                consumer.seek_to_beginning(tp)
                                for msg in consumer:
                                    events.append(msg.value)

                consumer.close()
            except Exception as e:
                logger.warning("Kafka consume failed: %s", e)

        # Filter by type if requested
        if event_type:
            events = [e for e in events if e.get("type") == event_type]

        # Deduplicate by timestamp
        seen = set()
        unique_events = []
        for e in events:
            ts = e.get("timestamp", "")
            if ts not in seen:
                seen.add(ts)
                unique_events.append(e)

        # Sort by timestamp descending and limit
        unique_events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return unique_events[:limit]

    def get_stats(self) -> dict:
        """Get aggregate stats from all events."""
        events = self.get_recent_events(limit=9999)
        detections = [e for e in events if e.get("type") == "detection"]
        feedbacks = [e for e in events if e.get("type") == "feedback"]

        real_count = sum(1 for d in detections if d.get("result") == "REAL IMAGE")
        fake_count = sum(1 for d in detections if d.get("result") == "DEEPFAKE IMAGE")
        total = len(detections)

        avg_confidence = 0
        if detections:
            confs = [d.get("confidence", 0) for d in detections if d.get("confidence")]
            avg_confidence = sum(confs) / len(confs) if confs else 0

        return {
            "total_scans": total,
            "real_count": real_count,
            "fake_count": fake_count,
            "real_pct": round((real_count / total * 100), 1) if total else 0,
            "fake_pct": round((fake_count / total * 100), 1) if total else 0,
            "avg_confidence": round(avg_confidence * 100, 1),
            "total_feedback": len(feedbacks),
            "kafka_connected": self.connected,
        }

    @property
    def status(self) -> str:
        """Return current connection status."""
        if not self.enabled:
            return "disabled"
        return "connected" if self.connected else "fallback"
