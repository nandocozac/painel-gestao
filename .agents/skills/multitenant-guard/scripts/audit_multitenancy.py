"""
Auditor estático e de padrões para isolamento Multi-Empresa no Painel de Gestão.
Verifica o arquivo app.py buscando possíveis falhas de isolamento de dados.
"""

import sys
import os
import re

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
APP_FILE = os.path.join(PROJECT_ROOT, 'app.py')

def audit_multitenancy():
    print("=" * 60)
    print(" AUDITORIA DE ISOLAMENTO MULTI-EMPRESA (MULTITENANT GUARD)")
    print("=" * 60)

    if not os.path.exists(APP_FILE):
        print(f"[ERRO] Arquivo app.py nao encontrado em {APP_FILE}")
        return 1

    with open(APP_FILE, 'r', encoding='utf-8') as f:
        linhas = f.readlines()

    tabelas_isoladas = ['Cliente', 'OrdemServico', 'Transacao', 'Veiculo']
    alertas = []
    total_queries = 0

    # Padrões que podem indicar consulta sem filtro de empresa
    # Ex: Cliente.query.all(), OrdemServico.query.filter(...)
    padrao_query = re.compile(r'(\b(?:Cliente|OrdemServico|Transacao)\b)\.query\.(\w+)\((.*?)\)')

    for num_linha, linha in enumerate(linhas, 1):
        # Ignora linhas comentadas ou rotas do Master Admin
        linha_strip = linha.strip()
        if linha_strip.startswith('#'):
            continue

        matches = padrao_query.finditer(linha)
        for m in matches:
            model_name = m.group(1)
            method = m.group(2)
            args = m.group(3)
            total_queries += 1

            # Chamadas perigosas sem filtro
            if method in ['all', 'first'] and not args:
                # Se for rota de admin, pode ser tolerável, mas registramos
                alertas.append((num_linha, linha_strip, f"Consulta '{model_name}.query.{method}()' sem filtros!"))
            elif method in ['filter', 'filter_by']:
                if 'empresa_id' not in args and 'g.empresa' not in args:
                    alertas.append((num_linha, linha_strip, f"Filtro em '{model_name}' parece não conter verificação de empresa_id!"))

    print(f"\nTotal de queries auditadas em tabelas de clientes/OS/financeiro: {total_queries}")
    
    # Exclui alertas conhecidos que são legítimos no painel de administração geral
    alertas_criticos = []
    for num_linha, codigo, motivo in alertas:
        # Funções admin_... podem listar dados agregados se for o admin
        if 'admin_' in codigo or 'migrar_banco' in codigo or 'recuperar_banco' in codigo:
            continue
        alertas_criticos.append((num_linha, codigo, motivo))

    if alertas_criticos:
        print(f"\n[ALERTA] Foram encontrados {len(alertas_criticos)} pontos de atenção para revisão:")
        for num_linha, codigo, motivo in alertas_criticos:
            print(f"  Linha {num_linha}: {motivo}")
            print(f"    Código: {codigo}\n")
    else:
        print("\n[SUCESSO] Nenhuma violação crítica de isolamento multi-empresa detectada!")

    print("=" * 60)
    return 0

if __name__ == '__main__':
    sys.exit(audit_multitenancy())
