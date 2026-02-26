#!/bin/sh
set -e

echo "Esperando base de datos..."

# Espera a que la DB esté lista
until nc -z $DB_HOST $DB_PORT; do
  sleep 1
done

echo "Base de datos lista ✔"

echo "Aplicando migraciones..."
python manage.py migrate --noinput

echo "Recolectando estáticos..."
python manage.py collectstatic --noinput

echo "Iniciando Gunicorn..."
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120