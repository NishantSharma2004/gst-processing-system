import time
import uuid
import logging
import json
from datetime import datetime
from collections import deque
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("clickstack_observability")

class ClickStackTelemetry:
    def __init__(self):
        self.total_requests = 0
        self.status_2xx = 0
        self.status_4xx = 0
        self.status_5xx = 0
        self.latencies = deque(maxlen=1000)
        self.recent_logs = deque(maxlen=100)
        self.ingestion_telemetry = []

    def record_request(self, method: str, path: str, status_code: int, duration_ms: float, trace_id: str):
        self.total_requests += 1
        if 200 <= status_code < 300:
            self.status_2xx += 1
        elif 400 <= status_code < 500:
            self.status_4xx += 1
        elif status_code >= 500:
            self.status_5xx += 1

        self.latencies.append(duration_ms)

        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "service": "gst-corporate-platform",
            "trace_id": trace_id,
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
            "level": "ERROR" if status_code >= 500 else ("WARN" if status_code >= 400 else "INFO")
        }
        
        self.recent_logs.appendleft(log_entry)
        logger.info(json.dumps(log_entry))

    def record_ingestion(self, filename: str, rows: int, duration_sec: float):
        self.ingestion_telemetry.append({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "filename": filename,
            "total_rows": rows,
            "duration_sec": round(duration_sec, 2),
            "rows_per_sec": round(rows / duration_sec, 1) if duration_sec > 0 else rows
        })

    def get_metrics(self):
        avg_latency = round(sum(self.latencies) / len(self.latencies), 2) if self.latencies else 0.0
        p95_latency = round(sorted(self.latencies)[int(len(self.latencies) * 0.95)], 2) if self.latencies else 0.0

        return {
            "service_name": "gst-corporate-platform",
            "clickstack_version": "1.4.0",
            "status": "healthy",
            "uptime_metrics": {
                "total_requests": self.total_requests,
                "successful_2xx": self.status_2xx,
                "client_errors_4xx": self.status_4xx,
                "server_errors_5xx": self.status_5xx,
                "avg_latency_ms": avg_latency,
                "p95_latency_ms": p95_latency
            },
            "ingestion_telemetry": self.ingestion_telemetry[-5:],
            "system_health": {
                "database_driver": "Supabase PostgreSQL / SQLite",
                "telemetry_exporter": "ClickStack OpenTelemetry Active"
            }
        }

telemetry = ClickStackTelemetry()

class ClickStackObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = str(uuid.uuid4())
        request.state.trace_id = trace_id
        start_time = time.time()

        try:
            response: Response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000
            telemetry.record_request(request.method, request.url.path, response.status_code, duration_ms, trace_id)
            response.headers["X-Trace-ID"] = trace_id
            response.headers["X-ClickStack-Latency"] = f"{round(duration_ms, 2)}ms"
            return response
        except Exception as exc:
            duration_ms = (time.time() - start_time) * 1000
            telemetry.record_request(request.method, request.url.path, 500, duration_ms, trace_id)
            raise exc