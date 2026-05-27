# Deployment image: official Freqtrade + the olibuguard package installed so that
# user_data/strategies/OlibuguardStrategy.py can import olibuguard.*
# Development is done natively (uv); this container is for running the bot 24/7.
FROM freqtradeorg/freqtrade:stable

USER root

# Install only the olibuguard core (freqtrade is already in the base image).
WORKDIR /tmp/build
COPY pyproject.toml ./
COPY README.md ./README.md
COPY olibuguard ./olibuguard
RUN pip install --no-cache-dir ".[ai]" && rm -rf /tmp/build

WORKDIR /freqtrade
USER ftuser
