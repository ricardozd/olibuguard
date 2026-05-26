# Imagen de despliegue: Freqtrade oficial + el paquete olibuguard instalado,
# para que user_data/strategies/OlibuguardStrategy.py pueda importar olibuguard.*
# El desarrollo es nativo (uv); este contenedor es para correr el bot 24/7.
FROM freqtradeorg/freqtrade:stable

USER root

# Instala solo el núcleo olibuguard (freqtrade ya viene en la imagen base).
WORKDIR /tmp/build
COPY pyproject.toml ./
COPY README.md ./README.md
COPY olibuguard ./olibuguard
RUN pip install --no-cache-dir . && rm -rf /tmp/build

WORKDIR /freqtrade
USER ftuser
