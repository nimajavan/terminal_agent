FROM python:3.13-slim
WORKDIR /app
COPY . /app
ENV PYTHONDONTWRITEBYTECODE=1 XDG_DATA_HOME=/tmp/lta-data XDG_CONFIG_HOME=/tmp/lta-config
USER 65534:65534
CMD ["sh", "-c", "python -B -m unittest discover -s tests && python -B lta evaluate"]
