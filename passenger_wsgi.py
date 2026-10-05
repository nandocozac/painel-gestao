import os
import sys

# Garante que o diretório da aplicação seja o CWD e esteja no início do sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Importa a instância da aplicação Flask (o servidor LiteSpeed / Passenger espera a variável 'application')
from app import app as application

