import os
import sys

# Adiciona o diretório atual do projeto ao PATH do Python
sys.path.insert(0, os.path.dirname(__file__))

# Importa a instância da aplicação Flask (o servidor LiteSpeed / Passenger espera a variável 'application')
from app import app as application
