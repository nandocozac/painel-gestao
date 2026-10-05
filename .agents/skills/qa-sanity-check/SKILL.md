---
name: qa-sanity-check
description: >-
  Use this skill whenever making code changes, modifying database schemas,
  altering templates, or before performing a commit or deploy. It runs automated
  sanity checks to verify HTTP anti-cache headers, session cookies, database model integrity,
  and smoke tests across core application routes.
---

# QA Sanity Check (Garantia de Qualidade e Integridade)

Esta skill executa uma auditoria automatizada completa na aplicação para garantir que nenhuma alteração quebre o sistema, vaze dados ou degrade a segurança do Painel de Gestão.

## Quando usar esta Skill:
- Antes de fazer qualquer `git commit` ou `git push`.
- Após adicionar novas rotas ou alterar [app.py](file:///d:/Projetos/painel_gestao/app.py) ou [models.py](file:///d:/Projetos/painel_gestao/models.py).
- Antes de gerar pacotes de publicação para a Hostinger.

## Procedimento de Execução:

1. Execute o script de testes de sanidade no ambiente local:
   ```powershell
   .\venv\Scripts\python.exe .agents/skills/qa-sanity-check/scripts/run_checks.py
   ```

2. Analise os resultados:
   - **Cabeçalhos Anti-Cache**: Garante que `Cache-Control: no-cache, no-store, must-revalidate, max-age=0, private` e `X-LiteSpeed-Cache-Control: no-cache` estejam presentes em todas as respostas dinâmicas.
   - **Segurança de Sessão**: Valida `SESSION_COOKIE_HTTPONLY=True` e `SESSION_COOKIE_SAMESITE=Lax`.
   - **Regra de Logomarca**: Garante que nenhuma empresa receba imagem prévia da Valcar ou de terceiros (`logo_filename=None` por padrão).
   - **Segmentos**: Valida que a coluna `tipo_negocio` exista e suporte tanto `OFICINA` quanto `LOJA`.
   - **Smoke Tests**: Valida respostas HTTP esperadas para páginas iniciais e autenticação.

3. Se houver falhas, corrija os apontamentos imediatamente antes de prosseguir.
