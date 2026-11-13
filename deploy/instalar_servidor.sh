#!/usr/bin/env bash
# Prepara un servidor Ubuntu 24.04 (Oracle Cloud, ARM) para Proyecto Empleo.
# Uso:  bash deploy/instalar_servidor.sh
set -euo pipefail

echo "==> Actualizando el sistema"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

echo "==> Instalando Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"

echo "==> Zona horaria America/Bogota"
sudo timedatectl set-timezone America/Bogota

echo "==> Abriendo puertos 80 y 443 en el firewall del sistema"
# Las imagenes de Oracle traen iptables cerrado salvo SSH; ademas hay que abrirlos en la Security List de la VCN.
for p in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport "$p" -j ACCEPT
done
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
sudo netfilter-persistent save

echo "==> Memoria swap de 2 GB (para compilar el frontend)"
if [ ! -f /swapfile ]; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Actualizaciones de seguridad automaticas"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y unattended-upgrades
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

echo
echo "Listo. Cierre la sesion SSH y vuelva a entrar para usar docker sin sudo."
