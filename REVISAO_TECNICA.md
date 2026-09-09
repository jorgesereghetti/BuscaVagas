# Revisão técnica — BuscaVagas

Revisão realizada em 08/09/2026. Foram examinados a API Flask, o banco SQLite, os quatro coletores, os provedores de IA, o ranking semântico, a interface e o script de inicialização. As correções mantêm a estrutura atual do projeto.

As sugestões desta revisão também foram implementadas: dados estruturados dos anúncios passaram a ser extraídos quando disponíveis; campos ausentes deixam de receber valores inventados; política de data, score mínimo e localização desconhecida tornou-se configurável; falhas por fonte aparecem no diagnóstico; cancelamento e fila recuperável da busca são persistidos no SQLite; identidades são consultadas em lote por fonte/URL; o fluxo foi consolidado em Novas, Enviadas e Não Compatíveis; autenticação Basic opcional e bloqueio de acesso externo sem senha foram adicionados; e `requirements.lock` registra dependências reproduzíveis.

## Correções aplicadas

| Problema encontrado | Correção |
|---|---|
| Termos e exclusões editados na interface não eram usados pelo motor, que guardava referências antigas dos imports. | Cada busca captura as configurações atuais em `backend/engine.py`. |
| Salvar consultas/exclusões fazia um GET redundante e podia apagar configurações caso esse GET falhasse. | O frontend envia apenas os campos alterados; a API preserva os restantes. |
| JSON com listas, números ou tipos errados podia gerar erro 500 ou ser convertido indevidamente em termos de busca. | Validação de objeto JSON, strings e listas; entradas inválidas recebem 400. Termos vazios e duplicados são removidos. |
| Uma lista de exclusões contendo apenas espaços gerava regex que correspondia a qualquer palavra. | Lista efetivamente vazia não exclui títulos. |
| Escrita direta podia deixar currículo/configuração incompletos em caso de interrupção durante a gravação. | Arquivo temporário seguido de substituição; gravações locais serializadas. |
| A deduplicação confundia `/jobs/123` com `/jobs/1234` devido ao uso de `LIKE 'url%'`. | Comparação exata de URLs normalizadas, com compatibilidade para tracking em registros antigos. |
| `INSERT OR REPLACE` podia substituir uma candidatura existente e reiniciar sua data de inclusão. | Conflitos preservam o registro; apenas falhas legadas de IA ainda ocultadas podem ser reavaliadas. |
| Falha técnica da IA era salva como vaga rejeitada. | Novas falhas ficam disponíveis para outra tentativa, sem inserir uma rejeição fictícia. |
| Embeddings eram calculados também para vagas já avaliadas. | A consulta ao banco precede o ranking semântico. |
| A localização coletada não chegava explicitamente à avaliação da IA. | O texto enviado ao modelo agora inclui a localização informada pela fonte. |
| A string `"false"` era interpretada como verdadeira pela conversão booleana. | Normalização explícita da validade; notas infinitas e justificativas de tipo inválido recebem tratamento defensivo. |
| Paginação podia insistir em páginas repetidas, inclusive sem nenhuma vaga aprovada. | Gupy e LinkedIn encerram a coleta ao detectar repetição de página. |
| Desfazer exclusão só recolocava a vaga na tela. | O desfazer restaura o status pela API, inclusive após atualização da listagem, sem duplicação local. |
| Erros HTTP ao excluir eram tratados como sucesso; falhas de status deixavam contadores desatualizados. | Verificação de `response.ok` e reversão dos dados/contadores. |
| Recarregar a página durante uma busca não retomava o acompanhamento. | A interface reinicia o polling quando encontra busca ativa e o encerra ao concluir. |
| O botão de limpeza profunda não tinha evento associado, e seu texto omitia que apagava vagas novas. | Ação conectada e escopo esclarecido antes da confirmação. Nenhuma limpeza foi executada nesta revisão. |
| Links externos e IDs entravam em atributos HTML sem proteção suficiente; CSV podia conter fórmulas de fontes externas. | URLs limitadas a HTTP/HTTPS, escape de atributos/argumentos e neutralização de prefixos de fórmula no CSV. |
| O servidor sem autenticação escutava em todas as interfaces por padrão. | Padrão alterado para `127.0.0.1`; `HOST=0.0.0.0` continua disponível explicitamente. |
| O script anunciava Python 3.9, incompatível com anotações usadas em módulos do projeto. | Requisito e verificação ajustados para Python 3.10+; instalação via `python -m pip`. |

A limpeza de redundâncias centralizou HTML/URLs em `backend/parsing.py`, reutilizou os aliases de status e a atualização de status, removeu função/propriedade/parâmetro sem uso e avisos de sucesso duplicados. O código deixa de criar um índice redundante sobre a URL, que já possui restrição UNIQUE; índices existentes no banco real não foram removidos. Arquivos de cache Python são gerados automaticamente e já estão no `.gitignore`.

## Validação

- **26 testes Python aprovados**, com SQLite temporário e serviços externos simulados: API, deduplicação, migração de estados, preservação de candidaturas, reavaliação, ranking, localização, parsing, paginação, gravação interrompida, JSON-LD, cancelamento, retomada e política.
- **12 testes JavaScript aprovados**, com DOM e rede simulados: desfazer, erros HTTP, contadores, polling, edição parcial, URLs, dados ausentes e estrutura do Kanban.
- A página pública do Programathor foi validada com `JobPosting` JSON-LD e agora fornece empresa, regime, data de publicação, validade e descrição quando publicados. O endpoint RSS antigo da Remotar respondeu 404 durante a validação; esse erro agora aparece no diagnóstico da fonte em vez de gerar vagas fictícias.
- Sintaxe Python, JavaScript e Bash validada; `pip check` não encontrou dependências incompatíveis no ambiente instalado.
- Banco real, currículo e credenciais preservados. Nenhuma busca real ou chamada de IA foi disparada. A interface foi revalidada em navegador real em 16/09/2026, sem erros de console e com as três colunas esperadas; a disponibilidade atual das fontes/modelos não foi validada.

Para repetir:

```bash
.venv/bin/python -m unittest discover -s tests -v
node --test tests/frontend.test.cjs
.venv/bin/python -m pip check
```

## O que permanece para uma próxima etapa

1. **Remotar:** o endpoint RSS antigo retornou 404. O coletor registra o alerta e não inventa vagas; falta descobrir o endpoint oficial atual ou desativar essa fonte até haver uma integração estável.
2. **Execução em produção:** o lease persistido resolve concorrência entre processos, mas o Flask ainda deve ser servido por WSGI em produção. A autenticação Basic opcional deve ser habilitada antes de usar `HOST=0.0.0.0`: [Deploying to Production](https://flask.palletsprojects.com/en/stable/deploying/).
3. **Observabilidade:** o diagnóstico por fonte já está disponível em `/api/scan/status`; uma próxima iteração pode guardar histórico e métricas de cada consulta para acompanhar disponibilidade ao longo do tempo.

As verificações de HTTP no frontend seguem o comportamento documentado de `fetch`: respostas 4xx/5xx não rejeitam automaticamente a Promise. Referência: [MDN — Using Fetch](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch). Para uma futura revisão de conexões/transações SQLite, o contexto `with connection` não fecha a conexão por si só: [Python — sqlite3](https://docs.python.org/3/library/sqlite3.html#how-to-use-the-connection-context-manager).

Reinicie o servidor para carregar o código alterado. O endereço local continua sendo `http://localhost:5001`, salvo configuração de porta. O acesso por outros dispositivos agora exige configurar `HOST` explicitamente.
