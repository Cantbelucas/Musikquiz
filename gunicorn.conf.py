import os

bind = "0.0.0.0:8000"
worker_class = "gthread"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = 120
graceful_timeout = 30
keepalive = 5

# Opret databasen én gang i master-processen, før workers forkes
preload_app = True

# /dev/shm er altid skrivbar, også med read_only rootfs
worker_tmp_dir = "/dev/shm"

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
