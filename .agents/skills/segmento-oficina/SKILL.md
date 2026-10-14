---
name: segmento-oficina
description: >-
  Use this skill whenever working on features, templates, models, reports, or workflows
  for automotive workshops and repair centers (empresa.tipo_negocio == 'OFICINA'). It ensures
  full support for vehicle management (plates, models, km), mechanics with technical specialties,
  preventive maintenance returns, and workshop-specific terms without leaking into retail businesses.
---

# Segmento Oficina Automotiva (OFICINA)

Esta skill orienta o desenvolvimento, arquitetura e validação de todas as funcionalidades dedicadas a **oficinas mecânicas, auto centers, funilarias, autoelétricas e centros automotivos**.

---

## 1. Princípio Fundamental do Segmento OFICINA

Para empresas com `empresa.tipo_negocio == 'OFICINA'`:
- O **veículo é entidade central** do atendimento (vinculado ao cliente e à Ordem de Serviço).
- Os serviços são prestados por **Mecânicos / Técnicos especializados** (com especialidades como Motor, Suspensão, Câmbio, Freios, Injeção Eletrônica).
- O documento principal é denominado **Ordem de Serviço (OS)** ou **Orçamento de Manutenção**.
- O pós-venda opera com foco em **Revisões Preventivas / Retornos** (troca de óleo, pastilhas de freio, revisão de 90 dias ou 10.000 km).

---

## 2. Padrões de Vocabulário & Interface (UI/UX)

Sempre que a empresa ativa for `OFICINA`, os seguintes termos e padrões devem ser adotados:

| Elemento | Termo Mandatório em OFICINA |
| :--- | :--- |
| **Documento** | "Ordem de Serviço" ou "OS #XXXX" |
| **Abertura** | "Nova Ordem de Serviço" / "Abertura de OS" |
| **Executores de Serviço** | "Mecânico(s)" / "Mão de Obra Especializada" |
| **Entrada do Atendimento** | "Problema Relatado na Entrada / Queixas do Veículo" |
| **Finalização** | "Veículo Pronto para Retirada" / "Concluir Manutenção" |
| **Retorno / Pós-Venda** | "Próxima Revisão Preventiva (Retorno)" |
| **Comissão Técnica** | "Comissão sobre Mão de Obra / Serviços Mecânicos" |

---

## 3. Diretrizes de Codificação & Templates

### Em Templates Jinja2:
```jinja2
<!-- Exemplo de Renderização Condicional de Veículo -->
{% if empresa.tipo_negocio == 'OFICINA' %}
<div class="bloco-veiculo">
    <label>Veículo / Placa:</label>
    <span>{{ os.veiculo.modelo }} - Placa: {{ os.veiculo.placa }}</span>
</div>
{% endif %}

<!-- Exemplo de Título de Documento -->
<h1 class="titulo">
    {% if empresa.tipo_negocio == 'OFICINA' %}Ordem de Serviço{% else %}Pedido de Venda{% endif %}
</h1>
```

### No Backend (Python / Flask):
- Ao criar ou salvar uma OS em modo `OFICINA`, sempre capture e vincule `veiculo_placa` e `veiculo_modelo`.
- Utilize `Cliente.veiculos` para sugerir veículos já cadastrados do cliente.
- Mecânicos devem ser filtrados com `Colaborador.funcao.in_(['MECANICO', 'TECNICO'])`.
- Ao registrar múltiplos mecânicos, armazene a `especialidade` de cada profissional (ex: *"Câmbio e Transmissão"*, *"Injeção Eletrônica"*).

---

## 4. Auditoria de Integridade OFICINA

Para auditar se as telas e fluxos automotivos estão funcionando sem omissões de campos de veículo:
```powershell
.\venv\Scripts\python.exe .agents/skills/segmento-oficina/scripts/check_oficina_integrity.py
```
