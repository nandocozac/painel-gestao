"""
Script de Backup e Verificação de Integridade SQLite do Painel de Gestão.
Utiliza a API oficial sqlite3.backup() para criar cópias atômicas e seguras mesmo com o banco em uso.
"""

import os
import sys
import sqlite3
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
INSTANCE_DIR = os.path.join(PROJECT_ROOT, 'instance')
DB_PATH = os.path.join(INSTANCE_DIR, 'painel_gestao.db')
BACKUPS_DIR = os.path.join(INSTANCE_DIR, 'backups')

def make_backup():
    print("=" * 60)
    print(" ROTINA DE BACKUP E INTEGRIDADE SQLITE")
    print("=" * 60)

    if not os.path.exists(DB_PATH):
        print(f"[ERRO] Banco de dados nao encontrado em: {DB_PATH}")
        return 1

    os.makedirs(BACKUPS_DIR, exist_ok=True)

    # 1. Verificação de integridade antes do backup
    print("\n[1/3] Verificando integridade física do SQLite (PRAGMA integrity_check)...")
    try:
        con_source = sqlite3.connect(DB_PATH)
        cur = con_source.cursor()
        resultado = cur.execute("PRAGMA integrity_check").fetchall()
        if resultado and resultado[0][0] == 'ok':
            print("  [OK] Banco de dados esta 100% integro (ok)!")
        else:
            print(f"  [AVISO] Verificacao retornou: {resultado}")
    except Exception as e:
        print(f"  [ERRO] Falha ao verificar banco: {e}")
        return 1

    # 2. Criação da cópia atômica com timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"painel_gestao_backup_{timestamp}.db"
    backup_dest = os.path.join(BACKUPS_DIR, backup_filename)

    print(f"\n[2/3] Gerando snapshot atomico do banco...")
    try:
        con_dest = sqlite3.connect(backup_dest)
        with con_dest:
            con_source.backup(con_dest, pages=100)
        con_dest.close()
        con_source.close()

        tamanho_kb = os.path.getsize(backup_dest) / 1024
        print(f"  [OK] Backup criado com sucesso: {backup_filename} ({tamanho_kb:.1f} KB)")
    except Exception as e:
        print(f"  [ERRO] Falha ao realizar backup: {e}")
        return 1

    # 3. Listagem de backups existentes
    print(f"\n[3/3] Backups disponiveis em instance/backups/:")
    backups = sorted([f for f in os.listdir(BACKUPS_DIR) if f.endswith('.db')], reverse=True)
    for b in backups[:5]:
        b_path = os.path.join(BACKUPS_DIR, b)
        b_size = os.path.getsize(b_path) / 1024
        print(f"  - {b} ({b_size:.1f} KB)")

    print("\n" + "=" * 60)
    print(" PROCESSO DE BACKUP CONCLUIDO COM SUCESSO!")
    print("=" * 60)
    return 0

if __name__ == '__main__':
    sys.exit(make_backup())
