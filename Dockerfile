# Build stage
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Final stage
FROM python:3.12-slim
WORKDIR /app
# Copy Python packages and Streamlit executable
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
RUN echo "deb http://deb.debian.org/debian bookworm main contrib non-free" > /etc/apt/sources.list \
    && echo "deb http://deb.debian.org/debian-security bookworm-security main contrib non-free" >> /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y libaio1 unzip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
COPY . .
RUN mkdir -p Files Bulk_Import reports logs tests
EXPOSE 8501
CMD ["streamlit", "run", "restaurantapp_st_cloud.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]