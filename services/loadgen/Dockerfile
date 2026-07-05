FROM python:3.11-slim

WORKDIR /srv
RUN pip install --no-cache-dir httpx==0.28.1

COPY loadgen.py .
CMD ["python", "loadgen.py"]
