#!/bin/sh
# Start jádra v kontejneru na NASu. Viz nas/docker-compose.yml.
set -e
cd /app

# Knihovny ve vlastním svazku: instalují se jednou, ne při každém startu.
if [ ! -x /venv/bin/python ]; then
    python -m venv /venv
fi
/venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt

mkdir -p data
exec /venv/bin/python -m trading dashboard --host 0.0.0.0 --port 8766 \
    --kniha data/portfolio.csv
