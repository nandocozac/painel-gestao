"""
Módulo de Integração com Mercado Pago (Assinaturas e Mensalidades SaaS)
Painel de Gestão - 2026
"""

import time
import requests
from datetime import date, timedelta
from database import db
from models import Empresa

MP_API_URL = "https://api.mercadopago.com"
DEFAULT_ACCESS_TOKEN = "APP_USR-8367202767793031-100215-34ce1dd4ef26c49b3f2a97f844fd6334-3731345293"
DEFAULT_PUBLIC_KEY = "APP_USR-cf109e45-06eb-41d2-910d-f7a90012463c"


def obter_credenciais_master():
    """Retorna o Access Token e Public Key configurados na empresa Master"""
    master = Empresa.query.filter_by(is_admin=True).first()
    access_token = (master.mercadopago_access_token if master and master.mercadopago_access_token else "").strip() or DEFAULT_ACCESS_TOKEN
    public_key = (master.mercadopago_public_key if master and master.mercadopago_public_key else "").strip() or DEFAULT_PUBLIC_KEY
    return access_token, public_key, master


def criar_preferencia_checkout(empresa_compradora, plano='MENSAL', tipo_plano='BASICO', base_url=''):
    """
    Cria uma preferência de pagamento (Checkout Pro) no Mercado Pago.
    Permite pagamento imediato via Pix, Cartão de Crédito ou Boleto.
    """
    access_token, _, master = obter_credenciais_master()

    if not access_token:
        return {"success": False, "error": "Credenciais do Mercado Pago não configuradas pelo administrador."}

    # Definir valores de acordo com a tabela do Administrador Master
    plano_upper = plano.upper()
    tipo_upper = tipo_plano.upper()

    if tipo_upper == 'FISCAL':
        if plano_upper == 'ANUAL':
            valor = master.valor_anual_fiscal if (master and master.valor_anual_fiscal) else 699.90
            titulo = f"Painel de Gestão - Plano Fiscal Anual (12 Meses)"
        else:
            valor = master.valor_mensalidade_fiscal if (master and master.valor_mensalidade_fiscal) else 79.90
            titulo = f"Painel de Gestão - Plano Fiscal Mensal (30 Dias)"
    else:
        if plano_upper == 'ANUAL':
            valor = master.valor_anual if (master and master.valor_anual) else 249.90
            titulo = f"Painel de Gestão - Plano Anual (12 Meses)"
        else:
            valor = master.valor_mensalidade if (master and master.valor_mensalidade) else 29.90
            titulo = f"Painel de Gestão - Plano Mensal (30 Dias)"

    external_ref = f"EMP_{empresa_compradora.id}_{plano_upper}_{tipo_upper}_{int(time.time())}"

    # URLs de retorno e webhook
    base_clean = base_url.rstrip('/') if base_url else "http://localhost:5000"
    success_url = f"{base_clean}/assinatura/retorno"
    failure_url = f"{base_clean}/assinatura/bloqueada"
    notification_url = f"{base_clean}/webhook/mercadopago"

    payload = {
        "items": [
            {
                "id": f"plano_{plano_upper.lower()}_{tipo_upper.lower()}",
                "title": titulo,
                "description": f"Assinatura do sistema para {empresa_compradora.nome_empresa}",
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": round(float(valor), 2)
            }
        ],
        "payer": {
            "name": (empresa_compradora.nome_empresa or "Cliente")[:30],
            "email": empresa_compradora.email or "contato@empresa.com"
        },
        "back_urls": {
            "success": success_url,
            "pending": success_url,
            "failure": failure_url
        },
        "auto_return": "approved",
        "external_reference": external_ref,
        "statement_descriptor": "PAINEL GESTAO"
    }

    # Se a URL não for localhost/IP local, adiciona notification_url pública
    if not any(local in base_clean for local in ['localhost', '127.0.0.1', '0.0.0.0']):
        payload["notification_url"] = notification_url

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(f"{MP_API_URL}/checkout/preferences", json=payload, headers=headers, timeout=15)
        if resp.status_code in [200, 201]:
            data = resp.json()
            return {
                "success": True,
                "init_point": data.get("init_point"),
                "sandbox_init_point": data.get("sandbox_init_point"),
                "preference_id": data.get("id"),
                "external_reference": external_ref,
                "valor": valor,
                "titulo": titulo
            }
        else:
            return {
                "success": False,
                "error": f"Erro do Mercado Pago ({resp.status_code}): {resp.text}"
            }
    except Exception as e:
        return {"success": False, "error": f"Falha de conexão com Mercado Pago: {str(e)}"}


def consultar_pagamento(payment_id, access_token=None):
    """Consulta os detalhes e o status de um pagamento na API do Mercado Pago"""
    if not access_token:
        access_token, _, _ = obter_credenciais_master()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    try:
        resp = requests.get(f"{MP_API_URL}/v1/payments/{payment_id}", headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[Mercado Pago] Erro ao consultar pagamento {payment_id}: {e}")
    return None


def ativar_assinatura_por_referencia(external_ref, payment_id=None):
    """
    Processa a ativação da assinatura a partir da external_reference.
    Formato esperado: EMP_{empresa_id}_{PLANO}_{TIPO_PLANO}_{TIMESTAMP}
    Exemplo: EMP_2_MENSAL_BASICO_1740000000
    """
    if not external_ref or not external_ref.startswith("EMP_"):
        return False, None, "Referência externa inválida."

    partes = external_ref.split("_")
    if len(partes) < 4:
        return False, None, "Formato de referência incompleto."

    try:
        empresa_id = int(partes[1])
        plano = partes[2].upper() # 'MENSAL' ou 'ANUAL'
        tipo_plano = partes[3].upper() # 'BASICO' ou 'FISCAL'
    except Exception as e:
        return False, None, f"Erro ao decodificar referência: {e}"

    empresa = db.session.get(Empresa, empresa_id)
    if not empresa:
        return False, None, f"Empresa #{empresa_id} não encontrada."

    hoje = date.today()
    dias = 365 if plano == 'ANUAL' else 30

    # Se a empresa já tinha validade futura, soma à data atual
    if empresa.data_validade and empresa.data_validade > hoje:
        empresa.data_validade = empresa.data_validade + timedelta(days=dias)
    else:
        empresa.data_validade = hoje + timedelta(days=dias)

    empresa.status_assinatura = 'ATIVO'

    if tipo_plano == 'FISCAL':
        empresa.permite_emissao_fiscal = True

    try:
        db.session.commit()
        msg = f"Assinatura {plano} liberada com sucesso até {empresa.data_validade.strftime('%d/%m/%Y')}!"
        return True, empresa, msg
    except Exception as e:
        db.session.rollback()
        return False, empresa, f"Erro ao salvar no banco: {e}"
