"""
Script de Auditoria de Integridade do Segmento OFICINA
Verifica se as rotas, modelos e templates contemplam adequadamente os campos
veiculares e termos automotivos quando a empresa estiver configurada como OFICINA.
"""

import sys
import os

# Adiciona a raiz do projeto ao path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, '..', '..', '..', '..'))
sys.path.insert(0, PROJECT_ROOT)

def main():
    print("=" * 60)
    print(" AUDITORIA DE INTEGRIDADE - SEGMENTO OFICINA AUTOMOTIVA")
    print("=" * 60)

    erros = 0

    # 1. Verificar templates cruciais
    templates_dir = os.path.join(PROJECT_ROOT, 'templates')
    os_form_path = os.path.join(templates_dir, 'os_form.html')
    os_print_path = os.path.join(templates_dir, 'os_print.html')
    os_pdf_path = os.path.join(templates_dir, 'os_cliente_pdf.html')

    print("\n[1/3] Verificando templates para campos de Veículo e Mecânico...")
    for path, nome in [(os_form_path, 'os_form.html'), (os_print_path, 'os_print.html'), (os_pdf_path, 'os_cliente_pdf.html')]:
        if not os.path.exists(path):
            print(f"  [ERRO] Template {nome} nao encontrado!")
            erros += 1
            continue

        with open(path, 'r', encoding='utf-8') as f:
            conteudo = f.read()

        tem_condicional = "empresa.tipo_negocio == 'OFICINA'" in conteudo or "tipo_negocio == 'OFICINA'" in conteudo
        tem_mecanico = "mecanico" in conteudo.lower()

        if tem_condicional and tem_mecanico:
            print(f"  [OK] {nome} contem suporte veicular e mecanicos condicionados!")
        else:
            print(f"  [ALERTA] {nome} pode estar faltando suporte a condicional de OFICINA.")
            erros += 1

    # 2. Verificar modelo Veiculo e OrdemServico
    print("\n[2/3] Verificando Modelos de Banco de Dados...")
    try:
        from models import Veiculo, OrdemServico, Colaborador
        assert hasattr(Veiculo, 'placa')
        assert hasattr(Veiculo, 'modelo')
        assert hasattr(OrdemServico, 'veiculo_id')
        assert hasattr(Colaborador, 'funcao')
        print("  [OK] Modelos Veiculo, OrdemServico e Colaborador validos para OFICINA!")
    except Exception as e:
        print(f"  [ERRO] Falha ao verificar modelos automotivos: {e}")
        erros += 1

    # 3. Teste de Contexto OFICINA na aplicacao Flask
    print("\n[3/3] Verificando Datalist de Especialidades Mecanicas...")
    with open(os_form_path, 'r', encoding='utf-8') as f:
        conteudo_form = f.read()
    if 'datalist-especialidades-mecanico' in conteudo_form:
        print("  [OK] Sugestoes de especialidades tecnicas automotivas presentes!")
    else:
        print("  [ALERTA] Datalist de especialidades tecnicas nao localizado em os_form.html.")
        erros += 1

    print("\n" + "=" * 60)
    if erros == 0:
        print(" RESULTADO: SUCESSO! Modulo OFICINA integro e funcional.")
        print("=" * 60)
        sys.exit(0)
    else:
        print(f" RESULTADO: Encontrados {erros} apontamentos para revisao.")
        print("=" * 60)
        sys.exit(1)

if __name__ == '__main__':
    main()
