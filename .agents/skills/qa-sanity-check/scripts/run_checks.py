"""
Script automatizado de verificação de sanidade (QA Sanity Check) do Painel de Gestão.
Executa bateria de testes sem alterar o banco de dados de produção.
"""

import sys
import os

# Adiciona a raiz do projeto ao path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import app, db
from models import Empresa, Cliente, OrdemServico, Transacao

def run_checks():
    print("=" * 60)
    print(" INICIANDO QA SANITY CHECK - PAINEL DE GESTAO")
    print("=" * 60)
    erros = []
    sucessos = 0

    client = app.test_client()

    # 1. Teste de Cabeçalhos Anti-Cache (LiteSpeed / Proxies)
    print("\n[1/5] Verificando Cabeçalhos HTTP Anti-Cache...")
    try:
        res = client.get('/login')
        cc = res.headers.get('Cache-Control', '')
        xl = res.headers.get('X-LiteSpeed-Cache-Control', '')
        
        if 'no-store' in cc and 'no-cache' in cc and 'private' in cc and xl == 'no-cache':
            print("  [OK] Cabecalhos anti-cache rigorosamente ativos!")
            sucessos += 1
        else:
            msg = f"Cabecalhos anti-cache incorretos! CC: {cc} | XL: {xl}"
            print(f"  [ERRO] {msg}")
            erros.append(msg)
    except Exception as e:
        erros.append(f"Exceção ao testar cabecalhos: {e}")

    # 2. Teste de Seguranca de Cookies de Sessao
    print("\n[2/5] Verificando Configurações de Sessão e Cookies...")
    try:
        cookie_http_only = app.config.get('SESSION_COOKIE_HTTPONLY')
        cookie_samesite = app.config.get('SESSION_COOKIE_SAMESITE')
        if cookie_http_only is True and cookie_samesite == 'Lax':
            print("  [OK] Cookies de sessao configurados com HttpOnly e SameSite=Lax!")
            sucessos += 1
        else:
            msg = f"Configuracao de cookie insegura: HttpOnly={cookie_http_only}, SameSite={cookie_samesite}"
            print(f"  [ERRO] {msg}")
            erros.append(msg)
    except Exception as e:
        erros.append(f"Exceção ao testar cookies: {e}")

    # 3. Teste de Integridade de Modelos e Regra de Logo Padrão
    print("\n[3/5] Verificando Regras de Modelos e Imagem Prévia...")
    try:
        with app.app_context():
            # Verifica que Empresa.logo_filename tem default None
            default_logo = Empresa.__table__.columns['logo_filename'].default
            default_val = default_logo.arg if default_logo else None
            if default_val in [None, '']:
                print("  [OK] Modelo Empresa nao possui logo.png como default!")
                sucessos += 1
            else:
                msg = f"Modelo Empresa ainda possui default={default_val} para logo_filename!"
                print(f"  [ERRO] {msg}")
                erros.append(msg)
    except Exception as e:
        erros.append(f"Exceção ao testar modelos: {e}")

    # 4. Teste dos Modos de Negócio (OFICINA e LOJA)
    print("\n[4/5] Verificando Suporte aos Segmentos OFICINA e LOJA...")
    try:
        with app.app_context():
            col_tipo = Empresa.__table__.columns.get('tipo_negocio')
            if col_tipo is not None:
                print("  [OK] Coluna tipo_negocio presente no modelo Empresa!")
                sucessos += 1
            else:
                msg = "Coluna tipo_negocio ausente no modelo Empresa!"
                print(f"  [ERRO] {msg}")
                erros.append(msg)
    except Exception as e:
        erros.append(f"Exceção ao testar tipo_negocio: {e}")

    # 5. Teste de Acesso a Rotas Públicas Principais
    print("\n[5/5] Testando Rotas Principais (Smoke Test)...")
    rotas = [
        ('/', 200),
        ('/proposta', 200),
        ('/login', 200),
        ('/cadastro', 200),
    ]
    for rota, expected_code in rotas:
        try:
            res = client.get(rota)
            if res.status_code == expected_code:
                print(f"  [OK] Rota {rota} respondeu com status {res.status_code}")
                sucessos += 1
            else:
                msg = f"Rota {rota} retornou status {res.status_code}, esperado {expected_code}"
                print(f"  [ERRO] {msg}")
                erros.append(msg)
        except Exception as e:
            erros.append(f"Exceção ao acessar {rota}: {e}")

    # Relatório Final
    print("\n" + "=" * 60)
    if not erros:
        print(f" RESULTADO: SUCESSO TOTAL! {sucessos} verificacoes passaram sem erros.")
        print(" O sistema esta integro e pronto para uso/deploy.")
        print("=" * 60)
        return 0
    else:
        print(f" RESULTADO: FORAM ENCONTRADOS {len(erros)} ERROS:")
        for err in erros:
            print(f"   -> {err}")
        print("=" * 60)
        return 1

if __name__ == '__main__':
    exit_code = run_checks()
    sys.exit(exit_code)
