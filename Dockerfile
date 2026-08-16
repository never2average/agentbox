FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY controller/ ./controller/
COPY k8s_modules/ ./k8s_modules/
COPY schemas/ ./schemas/

USER 65532:65532

ENTRYPOINT ["python", "-m", "controller.main"]
