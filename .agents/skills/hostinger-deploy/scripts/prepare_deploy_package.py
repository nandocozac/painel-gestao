"""
Prepara o pacote de publicação (.zip) para a Hostinger de forma limpa e segura.
Nunca inclui o banco de dados de desenvolvimento (instance/), prevenindo sobrescrever o banco de produção!
"""

import os
import zipfile
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
ZIP_DEST = os.path.join(PROJECT_ROOT, 'publicacao_hostinger.zip')

EXCLUDE_DIRS = {'.git', 'venv', '__pycache__', 'instance', 'staging', '.agents'}
EXCLUDE_EXTS = {'.pyc', '.pyo', '.zip'}

def build_package():
    print("=" * 60)
    print(" GERANDO PACOTE DE PUBLICACAO HOSTINGER")
    print("=" * 60)

    if os.path.exists(ZIP_DEST):
        os.remove(ZIP_DEST)

    arquivos_incluidos = 0
    with zipfile.ZipFile(ZIP_DEST, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for foldername, subfolders, filenames in os.walk(PROJECT_ROOT):
            # Filtra diretórios excluídos
            subfolders[:] = [d for d in subfolders if d not in EXCLUDE_DIRS]

            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext in EXCLUDE_EXTS or filename == "publicacao_hostinger.zip":
                    continue

                filepath = os.path.join(foldername, filename)
                arcname = os.path.relpath(filepath, PROJECT_ROOT)
                zipf.write(filepath, arcname)
                arquivos_incluidos += 1

    tamanho_mb = os.path.getsize(ZIP_DEST) / (1024 * 1024)
    print(f"[SUCESSO] Pacote gerado com sucesso!")
    print(f"Destino: {ZIP_DEST}")
    print(f"Total de arquivos incluídos: {arquivos_incluidos}")
    print(f"Tamanho final: {tamanho_mb:.2f} MB")
    print("\nAVISO IMPORTANTE: A pasta 'instance/' (banco local) foi excluída propositalmente para proteger o banco de dados dos clientes na Hostinger.")
    print("=" * 60)
    return 0

if __name__ == '__main__':
    sys.exit(build_package())
