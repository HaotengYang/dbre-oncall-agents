FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shop_workload ./shop_workload

USER nobody
CMD ["python", "-m", "shop_workload.runner"]
