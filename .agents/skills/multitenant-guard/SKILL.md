---
name: multitenant-guard
description: >-
  Use this skill whenever creating or editing routes, querying models, or designing
  database transactions in the Painel de Gestão. It enforces strict multi-tenant data
  isolation so no enterprise can ever access, modify, or leak another enterprise's
  customers, orders, transactions, or vehicles.
---

# Multitenant Guard (Proteção e Isolamento Multi-Empresa)

Esta skill estabelece e audita as regras de isolamento de dados entre empresas assinantes no Painel de Gestão.

## Regras Obrigatórias de Codificação Multi-Empresa:

1. **Filtro Explícito em Toda Consulta de Dados**:
   - Sempre utilize `empresa_id = g.empresa.id` ao buscar ou alterar registros:
     ```python
     # CORRETO:
     cliente = Cliente.query.filter_by(id=cliente_id, empresa_id=g.empresa.id).first_or_404()
     os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()
     
     # PROIBIDO:
     cliente = Cliente.query.get(cliente_id) # Permite que usuário de Empresa A acesse dados de Empresa B!
     ```

2. **Criação de Registros com Vinculação Explícita**:
   - Ao instanciar `Cliente`, `OrdemServico`, `Transacao` ou `Veiculo`, sempre passe `empresa_id=g.empresa.id`:
     ```python
     novo = Cliente(
         empresa_id=g.empresa.id,
         nome=nome,
         telefone=telefone
     )
     ```

3. **Verificação de Decorators de Acesso**:
   - Toda rota que manipula dados de uma empresa DEVE ter o decorator `@login_required`.
   - Rotas de gerenciamento global devem usar `@admin_required`.

4. **Script de Auditoria**:
   - Para verificar o código contra possíveis vazamentos:
     ```powershell
     .\venv\Scripts\python.exe .agents/skills/multitenant-guard/scripts/audit_multitenancy.py
     ```
