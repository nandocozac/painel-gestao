# Diretrizes do Projeto Painel de Gestão (AGENTS.md)

Este documento estabelece as regras mandatórias de desenvolvimento, arquitetura e segurança para qualquer agente de IA ou desenvolvedor atuando neste repositório.

---

## 1. Isolamento Multi-Empresa Rigoroso (Zero Data Leak)
- **Toda consulta ORM** em tabelas operacionais (`Cliente`, `OrdemServico`, `Transacao`, `Veiculo`) DEVE filtrar explicitamente por `empresa_id = g.empresa.id`.
- É **estritamente proibido** utilizar consultas abertas como `Cliente.query.get(id)` ou `OrdemServico.query.all()`. Use sempre `filter_by(id=id, empresa_id=g.empresa.id).first_or_404()`.
- Novos cadastros devem sempre vincular explicitamente `empresa_id = g.empresa.id`.

---

## 2. Política de Logotipo & Identidade Visual (Sem Imagens Prévia)
- **Proibido usar logomarcas padrão de terceiros (ex: Valcar/logo.png)** como fallback para novas empresas.
- Se uma empresa não fez upload de logomarca própria:
  - `logo_filename` deve ser `None`.
  - Na barra de navegação, exibir o monograma/avatar com as duas primeiras letras em maiúsculas (`{{ empresa.nome_empresa[:2]|upper }}`) e o nome da empresa.
  - Nas telas de impressão/PDF de OS e Pedidos, exibir o nome da empresa em tipografia corporativa de destaque, sem imagem.
  - Na tela de configurações, exibir o aviso *"Sem logotipo cadastrado"* com botão para envio.

---

## 3. Suporte Multi-Segmento (OFICINA vs LOJA)
- O sistema atende oficinas e comércios em geral via coluna `tipo_negocio` na tabela `empresas`.
- **Modo OFICINA**: Exibe campos de veículo (placa, modelo, km) e termos como "Ordem de Serviço", "Mecânico", "Veículo pronto".
- **Modo LOJA**: Oculta campos veiculares, tratando o documento como "Venda / Pedido" e mensagens de "Pedido pronto para entrega / retirada".
- Qualquer nova funcionalidade deve respeitar condicionalmente o segmento da empresa ativa.

---

## 4. Otimização de Concorrência SQLite & Anti-Cache
- O banco de dados SQLite deve sempre operar com:
  - `PRAGMA journal_mode = WAL;`
  - `PRAGMA synchronous = NORMAL;`
  - `PRAGMA busy_timeout = 5000;`
- Todas as rotas dinâmicas devem responder com cabeçalhos anti-cache (`no-cache, no-store, must-revalidate, max-age=0, private` e `X-LiteSpeed-Cache-Control: no-cache`) para garantir que o LiteSpeed Web Server da Hostinger nunca sirva páginas ou cookies de um usuário/computador para outro.
- O arquivo `.htaccess` na raiz deve ser sempre mantido.

---

## 5. Preservação de Dados de Produção
- O banco de dados de produção (`instance/painel_gestao.db`) **nunca** deve ser sobrescrito por cópias locais de teste.
- A pasta `instance/` deve permanecer sempre no `.gitignore`.
- Antes de qualquer alteração de esquema no banco, execute a skill `db-backup-maintenance`.
- Antes de qualquer commit ou deploy, execute a skill `qa-sanity-check`.
