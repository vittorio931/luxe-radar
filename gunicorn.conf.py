import os


bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
try:
    workers = max(1, min(4, int(os.environ.get("WEB_CONCURRENCY", "1"))))
except ValueError:
    workers = 1
worker_class = "gthread"
threads = 4
timeout = 180
graceful_timeout = 30
keepalive = 5
max_requests = 500
max_requests_jitter = 75
limit_request_line = 4094
limit_request_fields = 60
limit_request_field_size = 8190
accesslog = "-"
errorlog = "-"
capture_output = True
preload_app = False
