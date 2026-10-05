"""
Módulo de Integração com a API da Focus NFe (FocusNFe v2)
Permite a emissão de NFS-e (Serviços/Mão de Obra) e NFC-e / NF-e (Peças/Comércio)
com suporte aos ambientes de Homologação (Testes) e Produção.
"""

import os
import requests
import uuid
from datetime import datetime

URL_HOMOLOGACAO = "https://homologacao.focusnfe.com.br"
URL_PRODUCAO = "https://api.focusnfe.com.br"

def get_base_url(ambiente='HOMOLOGACAO'):
    """Retorna a URL base de acordo com o ambiente selecionado na empresa."""
    if ambiente and ambiente.upper() == 'PRODUCAO':
        return URL_PRODUCAO
    return URL_HOMOLOGACAO


def autorizar_nfse(empresa, ordem_servico, itens_servico):
    """
    Envia uma NFS-e (Nota Fiscal de Serviços Eletrônica) para a Focus NFe.
    Ideal para oficinas mecânicas, reparos e mão de obra de serviços em geral.
    """
    token = empresa.fiscal_token_focus
    if not token:
        return False, "Token da Focus NFe não configurado para esta empresa."

    base_url = get_base_url(empresa.fiscal_ambiente)
    ref_uuid = f"os_{ordem_servico.id}_{uuid.uuid4().hex[:10]}"
    endpoint = f"{base_url}/v2/nfse?ref={ref_uuid}"

    cliente = ordem_servico.cliente

    # Tomador (Cliente que recebe a nota)
    tomador = {
        "razao_social": cliente.nome or "Consumidor Final",
        "email": getattr(cliente, 'email', '') or "",
        "telefone": getattr(cliente, 'telefone', '') or ""
    }

    cpf_cnpj = getattr(cliente, 'cpf_cnpj', '')
    if cpf_cnpj:
        doc_limpo = "".join(filter(str.isdigit, cpf_cnpj))
        if len(doc_limpo) == 11:
            tomador["cpf"] = doc_limpo
        elif len(doc_limpo) == 14:
            tomador["cnpj"] = doc_limpo

    # Monta a discriminação dos serviços executados
    discriminacao_linhas = []
    valor_total_servicos = 0.0

    for item in itens_servico:
        sub = float(item.subtotal or 0.0)
        valor_total_servicos += sub
        discriminacao_linhas.append(f"{item.descricao} (Qtd: {item.quantidade}): R$ {sub:.2f}")

    if not discriminacao_linhas:
        return False, "Nenhum item de serviço identificado para emissão de NFS-e."

    discriminacao_texto = " | ".join(discriminacao_linhas)
    if ordem_servico.veiculo:
        v = ordem_servico.veiculo
        discriminacao_texto += f" | Veículo: {v.modelo} (Placa: {v.placa})"

    # Código de tributação municipal (item da lista de serviços LC 116/03)
    item_codigo = "14.01" # Padrão nacional para reparação e manutenção mecânica/revisão
    for it in itens_servico:
        if getattr(it, 'codigo_servico_municipal', ''):
            item_codigo = it.codigo_servico_municipal
            break

    payload = {
        "data_emissao": datetime.now().isoformat(),
        "prestador": {
            "cnpj": "".join(filter(str.isdigit, empresa.fiscal_cnpj or "")),
            "inscricao_municipal": empresa.fiscal_inscricao_municipal or ""
        },
        "tomador": tomador,
        "servico": {
            "valor_servicos": round(valor_total_servicos, 2),
            "discriminacao": discriminacao_texto[:1000],
            "item_lista_servico": item_codigo,
            "iss_retido": False
        }
    }

    try:
        # A Focus NFe usa HTTP Basic Auth com o token como usuário e senha vazia
        response = requests.post(endpoint, json=payload, auth=(token, ''), timeout=30)
        dados = response.json() if response.text else {}

        if response.status_code in [200, 201, 202]:
            return True, {
                "referencia": ref_uuid,
                "status": dados.get("status", "PROCESSANDO"),
                "mensagem": dados.get("mensagem", "NFS-e enviada para processamento."),
                "dados_completos": dados
            }
        else:
            msg_erro = dados.get("mensagem") or dados.get("erros") or f"Erro {response.status_code}: {response.text}"
            return False, str(msg_erro)
    except Exception as e:
        return False, f"Falha de comunicação com a Focus NFe: {str(e)}"


def consultar_nfse(empresa, referencia_uuid):
    """Consulta o status de processamento da NFS-e na Focus NFe."""
    token = empresa.fiscal_token_focus
    if not token:
        return False, "Token não configurado."

    base_url = get_base_url(empresa.fiscal_ambiente)
    endpoint = f"{base_url}/v2/nfse/{referencia_uuid}"

    try:
        response = requests.get(endpoint, auth=(token, ''), timeout=20)
        dados = response.json() if response.text else {}

        if response.status_code == 200:
            status = dados.get("status", "").upper()
            return True, {
                "status": status,
                "numero": dados.get("numero", ""),
                "serie": dados.get("serie", ""),
                "url_danfe": dados.get("url_danfe", ""),
                "url_xml": dados.get("url_xml", ""),
                "mensagem": dados.get("mensagem", "")
            }
        else:
            return False, dados.get("mensagem", f"Erro {response.status_code}")
    except Exception as e:
        return False, str(e)


def autorizar_nfce(empresa, ordem_servico, itens_pecas):
    """
    Envia uma NFC-e (Nota Fiscal de Consumidor Eletrônica) para a SEFAZ via Focus NFe.
    Ideal para venda de peças, balcão de oficina e lojas do varejo.
    """
    token = empresa.fiscal_token_focus
    if not token:
        return False, "Token da Focus NFe não configurado para esta empresa."

    base_url = get_base_url(empresa.fiscal_ambiente)
    ref_uuid = f"nfce_{ordem_servico.id}_{uuid.uuid4().hex[:10]}"
    endpoint = f"{base_url}/v2/nfce?ref={ref_uuid}"

    cliente = ordem_servico.cliente

    itens_payload = []
    valor_produtos = 0.0

    for idx, item in enumerate(itens_pecas, 1):
        v_unit = float(item.valor_unitario or 0.0)
        qtd = float(item.quantidade or 1.0)
        sub = round(v_unit * qtd, 2)
        valor_produtos += sub

        ncm_item = getattr(item, 'ncm', '') or "87082999" # Peças e acessórios para veículos
        cfop_item = getattr(item, 'cfop', '') or "5102" # Venda de mercadoria adquirida de terceiros

        itens_payload.append({
            "numero_item": idx,
            "codigo_produto": str(item.id),
            "descricao": item.descricao,
            "codigo_ncm": "".join(filter(str.isdigit, ncm_item)),
            "cfop": "".join(filter(str.isdigit, cfop_item)),
            "unidade_comercial": "UN",
            "quantidade_comercial": qtd,
            "valor_unitario_comercial": round(v_unit, 2),
            "valor_total_bruto": sub,
            "unidade_tributavel": "UN",
            "quantidade_tributavel": qtd,
            "valor_unitario_tributavel": round(v_unit, 2),
            "icms_origem": 0,
            "icms_situacao_tributaria": "102" # Simples Nacional sem permissão de crédito
        })

    if not itens_payload:
        return False, "Nenhum produto ou peça identificada para emissão de NFC-e."

    forma_pgto = "01" # 01=Dinheiro, 02=Cheque, 03=Cartão Crédito, 04=Cartão Débito, 17=PIX
    if ordem_servico.forma_pagamento:
        fp_upper = ordem_servico.forma_pagamento.upper()
        if 'PIX' in fp_upper:
            forma_pgto = "17"
        elif 'CART' in fp_upper or 'CRÉDITO' in fp_upper or 'CREDITO' in fp_upper:
            forma_pgto = "03"
        elif 'DÉBITO' in fp_upper or 'DEBITO' in fp_upper:
            forma_pgto = "04"

    payload = {
        "cnpj_emitente": "".join(filter(str.isdigit, empresa.fiscal_cnpj or "")),
        "data_emissao": datetime.now().strftime("%Y-%m-%dT%H:%M:%S-03:00"),
        "natureza_operacao": "VENDA DE MERCADORIA",
        "forma_pagamento": 0, # 0=Pagamento à Vista
        "tipo_documento": 1,  # 1=Saída
        "local_destino": 1,   # 1=Operação interna
        "finalidade_emissao": 1,
        "consumidor_final": 1,
        "presenca_comprador": 1,
        "itens": itens_payload,
        "formas_pagamento": [
            {
                "forma_pagamento": forma_pgto,
                "valor_pagamento": round(valor_produtos, 2)
            }
        ]
    }

    try:
        response = requests.post(endpoint, json=payload, auth=(token, ''), timeout=30)
        dados = response.json() if response.text else {}

        if response.status_code in [200, 201, 202]:
            return True, {
                "referencia": ref_uuid,
                "status": dados.get("status", "PROCESSANDO"),
                "mensagem": dados.get("mensagem", "NFC-e enviada para a SEFAZ."),
                "dados_completos": dados
            }
        else:
            msg_erro = dados.get("mensagem") or dados.get("erros") or f"Erro {response.status_code}: {response.text}"
            return False, str(msg_erro)
    except Exception as e:
        return False, f"Falha de comunicação com a Focus NFe: {str(e)}"
