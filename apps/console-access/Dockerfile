FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends novnc websockify netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 6080

CMD ["/start.sh"]
