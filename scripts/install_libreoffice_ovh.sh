#!/usr/bin/env bash
set -euo pipefail

echo "=== EnnoSmart Office faithful preview / OVH ==="

sudo apt update

sudo apt install -y \
  libreoffice \
  libreoffice-writer \
  libreoffice-calc \
  libreoffice-impress \
  fonts-liberation \
  fonts-dejavu-core

if command -v libreoffice >/dev/null 2>&1; then
  echo "LibreOffice OK: $(command -v libreoffice)"
  libreoffice --version || true
else
  echo "ERREUR: libreoffice introuvable"
  exit 1
fi

echo ""
echo "Dans le venv EnnoSmart, vérifie ensuite :"
echo "pip install pymupdf python-docx openpyxl extract-msg"
echo ""
echo "Puis redémarre le backend."
