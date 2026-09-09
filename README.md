# BuscaVagas

Aplicação local para coletar vagas, avaliá-las com IA e organizá-las em um fluxo de três estados: **Novas**, **Enviadas** e **Não Compatíveis**.

## Requisitos

- Python 3.10 ou superior
- Uma chave do Gemini ou Groq, ou uma instalação local do Ollama

## Configuração

1. Copie `.env.example` para `.env` e configure o provedor de IA.
2. Copie `backend/resume.example.txt` para `backend/resume.txt` e substitua o conteúdo pelo seu currículo.
3. Opcionalmente, copie `backend/config.example.json` para `backend/config.json` para personalizar termos e políticas antes da primeira execução.

Os arquivos `.env`, `backend/resume.txt`, `backend/config.json` e `vagas.db` são locais e não são enviados ao Git.

## Execução

```bash
./run.sh
```

A aplicação ficará disponível em `http://127.0.0.1:5001` por padrão.

## Testes

```bash
.venv/bin/python -m unittest discover -s tests -v
node --test tests/frontend.test.cjs
```

## Segurança

O servidor aceita somente conexões locais sem autenticação. Para permitir acesso pela rede, configure `APP_PASSWORD_HASH` e defina `HOST` explicitamente. Nunca versione chaves, currículo, preferências pessoais ou o banco de vagas.
