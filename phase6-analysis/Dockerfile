FROM python:3.12-slim
WORKDIR /app
COPY phase2-workers/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt
COPY phase2-workers/common.py /app/common.py
COPY phase2-workers/nvidia_client.py /app/nvidia_client.py
COPY phase6-analysis/ /app/
CMD ["python", "-c", "print('analysis image ready — run discovery_agent.py')"]
