"""
Script de Auditoria de Isolamento do Segmento LOJA
Verifica se as telas e templates protegem as empresas do segmento LOJA
contra vazamento de campos veiculares e termos automotivos indesejados.
"""

import sys
import os

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, '..', '..', '..', '..'))
sys.path.insert(0, PROJECT_ROOT)

def main():
    print("=" * 60)
    print(" AUDITORIA DE ISOLAMENTO - SEGMENTO LOJA & COMERCIO")
    print("=" * 60)

    erros = 0
    templates_dir = os.path.join(PROJECT_ROOT, 'templates')

    # 1. Verificar se campos de placa em os_form e os_print estao condicionados a OFICINA
    print("\n[1/3] Verificando blindagem de campos de Veiculo/Placa...")
    arquivos_veiculo = ['os_form.html', 'os_print.html', 'os_cliente_pdf.html', 'dashboard.html']
    
    for nome in arquivos_veiculo:
        path = os.path.join(templates_dir, nome)
        if not os.path.exists(path):
            print(f"  [ERRO] Template {nome} nao encontrado!")
            erros += 1
            continue

        with open(path, 'r', encoding='utf-8') as f:
            conteudo = f.read()

        # Verifica se o termo veiculo_placa ou placa esta solto sem condicional
        if 'veiculo_placa' in conteudo or 'os.veiculo' in conteudo:
            if "tipo_negocio == 'OFICINA'" not in conteudo and 'empresa.tipo_negocio' not in conteudo:
                print(f"  [ALERTA] {nome} contem mencao a veiculo sem condicional de tipo_negocio!")
                erros += 1
            else:
                print(f"  [OK] {nome} protege campos veiculares com condicional de segmento!")
        else:
            print(f"  [OK] {nome} nao possui campos veiculares diretos.")

    # 2. Verificar se o menu base.html adapta OSs / Pedidos
    print("\n[2/3] Verificando Menu Base (OSs vs Pedidos)...")
    base_path = os.path.join(templates_dir, 'base.html')
    with open(base_path, 'r', encoding='utf-8') as f:
        base_conteudo = f.read()

    if "empresa.tipo_negocio == 'OFICINA'" in base_conteudo and "Pedidos" in base_conteudo:
        print("  [OK] Menu base alterna dinamicamente entre 'OSs' e 'Pedidos'!")
    else:
        print("  [ALERTA] Menu base pode nao estar alternando para 'Pedidos' em modo LOJA.")
        erros += 1

    # 3. Verificar Termo de Retornos Preventivos vs Pos-Venda
    print("\n[3/3] Verificando Rotulagem de Pos-Venda...")
    if "Pós-Venda" in base_conteudo or "Pos-Venda" in base_conteudo:
        print("  [OK] Pos-venda e adaptado para o segmento LOJA (exibe 'Pós-Venda' em vez de 'Retornos')!")
    else:
        print("  [AVISO] Verifique se os lembretes de pos-venda usam termos comerciais em modo LOJA.")

    print("\n" + "=" * 60)
    if erros == 0:
        print(" RESULTADO: SUCESSO! Modo LOJA esta isolado e sem vazamentos.")
        print("=" * 60)
        sys.exit(0)
    else:
        print(f" RESULTADO: Encontrados {erros} apontamentos para revisao.")
        print("=" * 60)
        sys.exit(1)

if __name__ == '__main__':
    main()
