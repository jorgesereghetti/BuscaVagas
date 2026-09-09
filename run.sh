#!/usr/bin/env bash
# ==============================================================================
# BuscaVagas AI — Script de Inicialização Rápida
# ==============================================================================

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=========================================="
echo "   BuscaVagas AI — Inicializando..."
echo "=========================================="

# 1. Checa Python 3
if ! command -v python3 &> /dev/null; then
    echo "❌ Erro: python3 não foi encontrado. Instale o Python 3.10+ para continuar."
    exit 1
fi

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "❌ Erro: é necessário Python 3.10 ou superior."
    exit 1
fi

# 2. Ambiente Virtual (.venv)
if [ ! -d ".venv" ] || [ ! -f ".venv/bin/python" ]; then
    echo "📦 Criando ambiente virtual (.venv)..."
    python3 -m venv .venv
fi

# 3. Ativação e Dependências
source .venv/bin/activate
echo "🔧 Verificando dependências..."
if [ -f "requirements.lock" ]; then
    python -m pip install -q -r requirements.lock
else
    python -m pip install -q -r requirements.txt
fi

# 4. Iniciar Servidor
export PORT="${PORT:-5001}"
echo ""
echo "🚀 Servidor iniciando em: http://localhost:$PORT"
echo "💡 Pressione Ctrl+C para encerrar."
echo "=========================================="

# Abre o navegador automaticamente no macOS (em segundo plano apos 1s)
(sleep 1.2 && open "http://localhost:$PORT" 2>/dev/null || true) &

# Executa o servidor Flask
exec python server.py
