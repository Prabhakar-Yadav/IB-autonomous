# Build context is the NemoClaw project root (see docker-compose.yml) so this
# image can bundle the worker code from every phase.
FROM python:3.12-slim
WORKDIR /app
COPY phase2-workers/requirements.txt /tmp/req-phase2.txt
COPY phase3-news-worker/requirements.txt /tmp/req-phase3.txt
RUN pip install --no-cache-dir -r /tmp/req-phase2.txt -r /tmp/req-phase3.txt
COPY phase2-workers /app/phase2
COPY phase3-news-worker /app/phase3
COPY phase4-analyst-surface/intake /app/phase4
COPY phase5-intelligence /app/phase5
COPY phase3-scheduler/scheduler.py /app/scheduler.py
CMD ["python", "-u", "scheduler.py"]
