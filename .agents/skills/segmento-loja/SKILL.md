---
name: segmento-loja
description: >-
  Use this skill whenever developing, modifying, or testing features for general retail,
  shops, commercial stores, or services (empresa.tipo_negocio == 'LOJA'). It guarantees
  that automotive terms (vehicles, plates, km, mechanics) are strictly hidden, enforcing
  a clean, professional retail order and sales workflow ("Venda / Pedido", "Orçamento", "Vendedor", "Entrega/Retirada").
---

# Segmento Loja & Comércio em Geral (LOJA)

Esta skill orienta o desenvolvimento, arquitetura e auditoria de funcionalidades para **empresas normais, comércio varejista, lojas, prestadores de serviços e vendas de balcão**.

---

## 1. Princípio de Ouro: Zero Herança Automotiva

Empresas comerciais com `empresa.tipo_negocio == 'LOJA'` **NÃO POSSUEM VEÍCULOS NEM MECÂNICOS**.
- É **terminantemente proibido** exibir campos de veículo (placa, modelo, km, chassi) ou termos mecânicos em empresas com `tipo_negocio == 'LOJA'`.
- Sempre que uma melhoria for feita no sistema, certifique-se de que ela **NÃO force campos automotivos** para empresas do segmento de loja.
- Telas de atendimento, formulários de venda, extratos e comprovantes PDF devem ser limpos, comerciais e objetivos.

---

## 2. Padrões de Vocabulário & Interface (UI/UX)

Sempre que a empresa ativa for `LOJA`, os seguintes termos e padrões devem ser adotados:

| Elemento | Termo Mandatório em LOJA | Termo Proibido em LOJA |
| :--- | :--- | :--- |
| **Documento** | "Venda / Pedido" ou "Pedido #XXXX" | ❌ "Ordem de Serviço" / "OS" |
| **Abertura** | "Novo Pedido / Venda" | ❌ "Abertura de OS" |
| **Equipe Responsável** | "Vendedor(es) / Comercial" | ❌ "Mecânico(s)" |
| **Serviço Executado** | "Mão de Obra / Serviço Contratado" | ❌ "Serviço Mecânico" |
| **Solicitação Inicial** | "Descrição dos Itens / Serviços Solicitados" | ❌ "Problema Relatado no Veículo" |
| **Status Pronto** | "Pedido Pronto para Retirada / Entrega" | ❌ "Veículo Pronto" |
| **Lembretes** | "Lembretes de Pós-Venda" | ❌ "Revisão Preventiva Automotiva" |

---

## 3. Diretrizes de Codificação & Templates

### Em Templates Jinja2:
```jinja2
<!-- 1. Ocultar completamente dados veiculares -->
{% if empresa.tipo_negocio == 'OFICINA' %}
<div class="bloco-veiculo">
    ... (Campos de Placa e Modelo NUNCA aparecem na LOJA) ...
</div>
{% endif %}

<!-- 2. Termos Dinâmicos Baseados no Segmento -->
<h1 class="text-base font-bold text-slate-900">
    {% if empresa.tipo_negocio == 'OFICINA' %}
        Ordem de Serviço
    {% else %}
        Pedido / Venda
    {% endif %}
</h1>

<!-- 3. Rótulos de Equipe -->
<label class="text-xs font-bold text-slate-700">
    {% if empresa.tipo_negocio == 'OFICINA' %}
        Mecânico(s) / Especialistas
    {% else %}
        Profissional(is) da Mão de Obra
    {% endif %}
</label>
```

### Na Geração de PDF e Comprovantes (`os_cliente_pdf.html` e `os_print.html`):
- O cabeçalho impresso para empresas `LOJA` deve exibir apenas os dados do Cliente e da Venda, sem nenhum campo "em branco" ou traço referente a veículo ou placa.
- Na aba de listagem, a coluna "Placa / Veículo" deve ser omitida ou substituída por "Itens / Resumo da Venda".

---

## 4. Auditoria de Isolamento LOJA

Para verificar se nenhuma tela ou documento está vazando campos ou termos automotivos para lojas comerciais:
```powershell
.\venv\Scripts\python.exe .agents/skills/segmento-loja/scripts/check_loja_isolation.py
```
