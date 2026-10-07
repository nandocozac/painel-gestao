import os
import base64
import csv
import re
import time
import shutil
import uuid
import zipfile
from io import StringIO, BytesIO
from datetime import date, datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, Response, send_file, session, g, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text, event, or_
from sqlalchemy.engine import Engine
from database import db
from models import Empresa, Cliente, Veiculo, OrdemServico, Transacao, ItemOS, NotaFiscal, Produto, Colaborador
from mercadopago_service import criar_preferencia_checkout, consultar_pagamento, ativar_assinatura_por_referencia

# Diretórios e caminhos absolutos para compatibilidade total local e em produção (Hostinger / LiteSpeed)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
os.makedirs(INSTANCE_DIR, exist_ok=True)
DB_PATH = os.path.join(INSTANCE_DIR, 'painel_gestao.db').replace('\\', '/')

app = Flask(__name__, instance_path=INSTANCE_DIR)
app.config['SECRET_KEY'] = 'chave-segura-painel-interno-multiempresa-2026'
# Conexão de Banco de Dados: Suporta PostgreSQL na nuvem (Render) ou SQLite local
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
    elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_NAME'] = 'painel_gestao_session'

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db.init_app(app)

# Otimização de concorrência e integridade SQLite para múltiplos workers / acessos simultâneos
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA busy_timeout = 5000")
        cursor.close()


# --- MIGRAÇÃO AUTOMÁTICA MULTI-EMPRESA E ADMINISTRAÇÃO SAAS ---

def migrar_banco_multiempresa():
    with app.app_context():
        db.create_all()

        is_sqlite = 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']
        if is_sqlite:
            # 1. Garantir que as novas colunas existam na tabela 'empresas' (execução antes de qualquer query SQLAlchemy no SQLite)
            colunas_empresas = [row[1] for row in db.session.execute(text("PRAGMA table_info(empresas)")).fetchall()]
            novas_colunas_empresas = {
                'is_admin': 'BOOLEAN DEFAULT 0',
                'status_assinatura': 'VARCHAR(20) DEFAULT "PENDENTE"',
                'data_validade': 'DATE',
                'observacoes_admin': 'TEXT DEFAULT ""',
                'chave_pix': 'VARCHAR(100) DEFAULT ""',
                'titular_pix': 'VARCHAR(100) DEFAULT "Fernando Cozac"',
                'valor_mensalidade': 'FLOAT DEFAULT 29.90',
                'valor_anual': 'FLOAT DEFAULT 249.90',
                'valor_mensalidade_fiscal': 'FLOAT DEFAULT 79.90',
                'valor_anual_fiscal': 'FLOAT DEFAULT 699.90',
                'tipo_negocio': 'VARCHAR(30) DEFAULT "OFICINA"',
                'permite_emissao_fiscal': 'BOOLEAN DEFAULT 0',
                'fiscal_ativo': 'BOOLEAN DEFAULT 0',
                'fiscal_ambiente': 'VARCHAR(20) DEFAULT "HOMOLOGACAO"',
                'fiscal_cnpj': 'VARCHAR(20) DEFAULT ""',
                'fiscal_razao_social': 'VARCHAR(150) DEFAULT ""',
                'fiscal_nome_fantasia': 'VARCHAR(150) DEFAULT ""',
                'fiscal_inscricao_municipal': 'VARCHAR(30) DEFAULT ""',
                'fiscal_inscricao_estadual': 'VARCHAR(30) DEFAULT ""',
                'fiscal_codigo_municipio': 'VARCHAR(10) DEFAULT "5208707"',
                'fiscal_regime_tributario': 'INTEGER DEFAULT 1',
                'fiscal_certificado_filename': 'VARCHAR(255)',
                'fiscal_certificado_senha': 'VARCHAR(255) DEFAULT ""',
                'fiscal_token_focus': 'VARCHAR(100) DEFAULT ""',
                'logo_base64': 'TEXT',
                'mercadopago_access_token': 'VARCHAR(255) DEFAULT ""',
                'mercadopago_public_key': 'VARCHAR(255) DEFAULT ""'
            }
            for col, col_type in novas_colunas_empresas.items():
                if col not in colunas_empresas:
                    db.session.execute(text(f"ALTER TABLE empresas ADD COLUMN {col} {col_type}"))
            db.session.commit()

            # 2. Garantir que as colunas 'empresa_id' existam nas tabelas de dados
            tabelas = ['clientes', 'ordens_servico', 'transacoes']
            for tab in tabelas:
                colunas = [row[1] for row in db.session.execute(text(f"PRAGMA table_info({tab})")).fetchall()]
                if 'empresa_id' not in colunas:
                    db.session.execute(text(f"ALTER TABLE {tab} ADD COLUMN empresa_id INTEGER REFERENCES empresas(id)"))
            db.session.commit()

            # 3. Garantir colunas fiscais e de produtos na tabela itens_os
            colunas_itens = [row[1] for row in db.session.execute(text("PRAGMA table_info(itens_os)")).fetchall()]
            novas_colunas_itens = {
                'produto_id': 'INTEGER REFERENCES produtos(id)',
                'ncm': 'VARCHAR(10) DEFAULT ""',
                'cfop': 'VARCHAR(10) DEFAULT ""',
                'codigo_servico_municipal': 'VARCHAR(20) DEFAULT ""',
                'aliquota_iss': 'FLOAT DEFAULT 0.0'
            }
            for col, col_type in novas_colunas_itens.items():
                if col not in colunas_itens:
                    db.session.execute(text(f"ALTER TABLE itens_os ADD COLUMN {col} {col_type}"))
            db.session.commit()

            # 4. Garantir colunas de rastreio e assinatura na tabela ordens_servico (SQLite)
            colunas_os = [row[1] for row in db.session.execute(text("PRAGMA table_info(ordens_servico)")).fetchall()]
            novas_colunas_os = {
                'numero_sequencial': 'INTEGER',
                'etapa_andamento': 'VARCHAR(30) DEFAULT "RECEBIDO"',
                'codigo_rastreio': 'VARCHAR(32)',
                'assinatura_cliente_data': 'TEXT',
                'assinatura_data_hora': 'TIMESTAMP'
            }
            for col, col_type in novas_colunas_os.items():
                if col not in colunas_os:
                    db.session.execute(text(f"ALTER TABLE ordens_servico ADD COLUMN {col} {col_type}"))
            db.session.commit()

            # 5. Garantir colunas de CPF/CNPJ e endereço na tabela clientes (SQLite)
            colunas_clientes = [row[1] for row in db.session.execute(text("PRAGMA table_info(clientes)")).fetchall()]
            novas_colunas_clientes = {
                'cpf_cnpj': 'VARCHAR(20) DEFAULT ""',
                'email': 'VARCHAR(120) DEFAULT ""',
                'endereco': 'VARCHAR(200) DEFAULT ""',
                'numero': 'VARCHAR(20) DEFAULT ""',
                'bairro': 'VARCHAR(100) DEFAULT ""',
                'cidade': 'VARCHAR(100) DEFAULT ""',
                'uf': 'VARCHAR(2) DEFAULT ""',
                'cep': 'VARCHAR(10) DEFAULT ""'
            }
            for col, col_type in novas_colunas_clientes.items():
                if col not in colunas_clientes:
                    db.session.execute(text(f"ALTER TABLE clientes ADD COLUMN {col} {col_type}"))
            db.session.commit()
        else:
            # Migração para PostgreSQL (Render Cloud)
            pg_colunas_empresas = {
                'is_admin': 'BOOLEAN DEFAULT FALSE',
                'status_assinatura': "VARCHAR(20) DEFAULT 'PENDENTE'",
                'data_validade': 'DATE',
                'observacoes_admin': "TEXT DEFAULT ''",
                'chave_pix': "VARCHAR(100) DEFAULT ''",
                'titular_pix': "VARCHAR(100) DEFAULT 'Fernando Cozac'",
                'valor_mensalidade': 'FLOAT DEFAULT 29.90',
                'valor_anual': 'FLOAT DEFAULT 249.90',
                'valor_mensalidade_fiscal': 'FLOAT DEFAULT 79.90',
                'valor_anual_fiscal': 'FLOAT DEFAULT 699.90',
                'tipo_negocio': "VARCHAR(30) DEFAULT 'OFICINA'",
                'permite_emissao_fiscal': 'BOOLEAN DEFAULT FALSE',
                'fiscal_ativo': 'BOOLEAN DEFAULT FALSE',
                'fiscal_ambiente': "VARCHAR(20) DEFAULT 'HOMOLOGACAO'",
                'fiscal_cnpj': "VARCHAR(20) DEFAULT ''",
                'fiscal_razao_social': "VARCHAR(150) DEFAULT ''",
                'fiscal_nome_fantasia': "VARCHAR(150) DEFAULT ''",
                'fiscal_inscricao_municipal': "VARCHAR(30) DEFAULT ''",
                'fiscal_inscricao_estadual': "VARCHAR(30) DEFAULT ''",
                'fiscal_codigo_municipio': "VARCHAR(10) DEFAULT '5208707'",
                'fiscal_regime_tributario': 'INTEGER DEFAULT 1',
                'fiscal_certificado_filename': 'VARCHAR(255)',
                'fiscal_certificado_senha': "VARCHAR(255) DEFAULT ''",
                'fiscal_token_focus': "VARCHAR(100) DEFAULT ''",
                'logo_base64': 'TEXT',
                'mercadopago_access_token': "VARCHAR(255) DEFAULT ''",
                'mercadopago_public_key': "VARCHAR(255) DEFAULT ''"
            }
            for col, col_type in pg_colunas_empresas.items():
                try:
                    db.session.execute(text(f"ALTER TABLE empresas ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            tabelas = ['clientes', 'ordens_servico', 'transacoes']
            for tab in tabelas:
                try:
                    db.session.execute(text(f"ALTER TABLE {tab} ADD COLUMN IF NOT EXISTS empresa_id INTEGER REFERENCES empresas(id)"))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            pg_colunas_itens = {
                'produto_id': "INTEGER REFERENCES produtos(id)",
                'ncm': "VARCHAR(10) DEFAULT ''",
                'cfop': "VARCHAR(10) DEFAULT ''",
                'codigo_servico_municipal': "VARCHAR(20) DEFAULT ''",
                'aliquota_iss': 'FLOAT DEFAULT 0.0'
            }
            for col, col_type in pg_colunas_itens.items():
                try:
                    db.session.execute(text(f"ALTER TABLE itens_os ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            pg_colunas_os = {
                'numero_sequencial': "INTEGER",
                'etapa_andamento': "VARCHAR(30) DEFAULT 'RECEBIDO'",
                'codigo_rastreio': "VARCHAR(32)",
                'assinatura_cliente_data': "TEXT",
                'assinatura_data_hora': "TIMESTAMP"
            }
            for col, col_type in pg_colunas_os.items():
                try:
                    db.session.execute(text(f"ALTER TABLE ordens_servico ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            pg_colunas_clientes = {
                'cpf_cnpj': "VARCHAR(20) DEFAULT ''",
                'email': "VARCHAR(120) DEFAULT ''",
                'endereco': "VARCHAR(200) DEFAULT ''",
                'numero': "VARCHAR(20) DEFAULT ''",
                'bairro': "VARCHAR(100) DEFAULT ''",
                'cidade': "VARCHAR(100) DEFAULT ''",
                'uf': "VARCHAR(2) DEFAULT ''",
                'cep': "VARCHAR(10) DEFAULT ''"
            }
            for col, col_type in pg_colunas_clientes.items():
                try:
                    db.session.execute(text(f"ALTER TABLE clientes ADD COLUMN IF NOT EXISTS {col} {col_type}"))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

        # 3. Garantir que a empresa Master exista de forma segura
        empresa_padrao = Empresa.query.filter_by(is_admin=True).first()
        if not empresa_padrao:
            empresa_padrao = Empresa.query.filter_by(email="nandocozac@gmail.com").first()
        if not empresa_padrao:
            empresa_padrao = Empresa.query.order_by(Empresa.id.asc()).first()

        if not empresa_padrao:
            empresa_padrao = Empresa(
                nome_empresa="SPOT MARKETING",
                subtitulo="Serviços Especializados e Atendimento Profissional",
                telefone="(62) 99494-1212",
                whatsapp="62994941212",
                endereco="",
                cidade_uf="",
                logo_filename=None,
                logo_base64=None,
                mensagem_rodape="Agradecemos a preferência! Volte sempre.",
                email="nandocozac@gmail.com",
                senha_hash=generate_password_hash("Matheus10#"),
                is_admin=True,
                status_assinatura="ATIVO",
                chave_pix="nandocozac@gmail.com",
                titular_pix="Fernando Cozac",
                valor_mensalidade=29.90,
                valor_anual=249.90,
                data_validade=date(2099, 12, 31)
            )
            db.session.add(empresa_padrao)
            db.session.commit()
        else:
            # Garantir que a empresa master continue sempre Master Admin e Ativa
            mudou = False
            if not empresa_padrao.is_admin:
                empresa_padrao.is_admin = True
                mudou = True
            if empresa_padrao.status_assinatura != 'ATIVO':
                empresa_padrao.status_assinatura = 'ATIVO'
                mudou = True
            if not empresa_padrao.data_validade or empresa_padrao.data_validade < date.today():
                empresa_padrao.data_validade = date(2099, 12, 31)
                mudou = True
            if not getattr(empresa_padrao, 'mercadopago_access_token', None):
                empresa_padrao.mercadopago_access_token = "APP_USR-8367202767793031-100215-34ce1dd4ef26c49b3f2a97f844fd6334-3731345293"
                mudou = True
            if not getattr(empresa_padrao, 'mercadopago_public_key', None):
                empresa_padrao.mercadopago_public_key = "APP_USR-cf109e45-06eb-41d2-910d-f7a90012463c"
                mudou = True
            if mudou:
                db.session.commit()

        # 4. Vincular dados anteriores existentes à empresa padrão
        try:
            db.session.execute(text(f"UPDATE clientes SET empresa_id = {empresa_padrao.id} WHERE empresa_id IS NULL"))
            db.session.execute(text(f"UPDATE ordens_servico SET empresa_id = {empresa_padrao.id} WHERE empresa_id IS NULL"))
            db.session.execute(text(f"UPDATE transacoes SET empresa_id = {empresa_padrao.id} WHERE empresa_id IS NULL"))
            # 5. Garantir que nenhuma empresa tenha 'logo.png' como padrão (sem imagem prévia até o cliente fazer upload)
            db.session.execute(text("UPDATE empresas SET logo_filename = NULL WHERE logo_filename = 'logo.png'"))
            db.session.commit()

            # 6. Gerar código de rastreio para ordens de serviço existentes que ainda não tenham
            os_sem_rastreio = OrdemServico.query.filter((OrdemServico.codigo_rastreio == None) | (OrdemServico.codigo_rastreio == '')).all()
            for os_item in os_sem_rastreio:
                os_item.codigo_rastreio = uuid.uuid4().hex[:12]
                if not os_item.etapa_andamento:
                    os_item.etapa_andamento = 'ENTREGUE' if os_item.status == 'CONCLUIDA' else 'RECEBIDO'
            if os_sem_rastreio:
                db.session.commit()

            # 7. Sincronização persistente de logotipos (evita perda em containers/redeploy efêmeros)
            empresas_todas = Empresa.query.all()
            for emp in empresas_todas:
                # Caso A: tem arquivo no disco mas ainda não tem base64 no banco -> migra para banco
                if emp.logo_filename and not emp.logo_base64:
                    caminho_logo = os.path.join(app.root_path, 'static', emp.logo_filename)
                    if os.path.exists(caminho_logo):
                        try:
                            ext = emp.logo_filename.rsplit('.', 1)[-1].lower() if '.' in emp.logo_filename else 'png'
                            mime = 'image/svg+xml' if ext == 'svg' else f'image/{ext if ext != "jpg" else "jpeg"}'
                            with open(caminho_logo, 'rb') as f_img:
                                b64 = base64.b64encode(f_img.read()).decode('utf-8')
                                emp.logo_base64 = f"data:{mime};base64,{b64}"
                        except Exception as e:
                            print(f"Erro ao converter logo para base64: {e}")
                # Caso B: tem base64 no banco mas arquivo no disco sumiu (novo deploy/disco efêmero) -> restaura arquivo no disco
                elif emp.logo_base64 and emp.logo_filename:
                    caminho_logo = os.path.join(app.root_path, 'static', emp.logo_filename)
                    if not os.path.exists(caminho_logo):
                        try:
                            os.makedirs(os.path.dirname(caminho_logo), exist_ok=True)
                            if ',' in emp.logo_base64:
                                _, b64_str = emp.logo_base64.split(',', 1)
                                with open(caminho_logo, 'wb') as f_out:
                                    f_out.write(base64.b64decode(b64_str))
                        except Exception as e:
                            print(f"Erro ao restaurar logo no disco a partir do base64: {e}")
            # 8. Garantir numeração sequencial isolada por empresa (iniciando em 1 para cada empresa)
            empresas_todas_ids = [e[0] for e in db.session.query(Empresa.id).all()]
            for eid in empresas_todas_ids:
                oss_empresa = OrdemServico.query.filter_by(empresa_id=eid).order_by(OrdemServico.id.asc()).all()
                seq = 1
                for o in oss_empresa:
                    if o.numero_sequencial != seq:
                        o.numero_sequencial = seq
                    seq += 1
            db.session.commit()
        except Exception:
            db.session.rollback()

migrar_banco_multiempresa()

def recuperar_banco_raiz_se_existir():
    """Garante que se existir um painel_gestao.db antigo na raiz do Hostinger, os dados sejam migrados para instance/"""
    if 'sqlite' not in app.config['SQLALCHEMY_DATABASE_URI']:
        return
    root_db = os.path.join(BASE_DIR, 'painel_gestao.db')
    if os.path.exists(root_db) and os.path.abspath(root_db) != os.path.abspath(DB_PATH):
        try:
            import sqlite3
            con_root = sqlite3.connect(root_db)
            cur_root = con_root.cursor()
            
            tabelas = [r[0] for r in cur_root.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if 'empresas' in tabelas:
                empresas_root = cur_root.execute("SELECT email, nome_empresa, senha_hash, telefone, whatsapp, status_assinatura, data_validade, is_admin FROM empresas").fetchall()
                with app.app_context():
                    for emp_data in empresas_root:
                        email, nome, senha_hash, tel, wa, status, validade, is_adm = emp_data
                        if not is_adm and email:
                            ja_existe = Empresa.query.filter_by(email=email).first()
                            if not ja_existe:
                                val_date = None
                                if validade:
                                    try:
                                        val_date = date.fromisoformat(str(validade)[:10])
                                    except Exception:
                                        val_date = date.today() + timedelta(days=30)
                                nova = Empresa(
                                    nome_empresa=nome or "Empresa Recuperada",
                                    email=email,
                                    senha_hash=senha_hash,
                                    telefone=tel or "",
                                    whatsapp=wa or "",
                                    status_assinatura=status or "ATIVO",
                                    data_validade=val_date,
                                    is_admin=False
                                )
                                db.session.add(nova)
                    db.session.commit()
            con_root.close()
        except Exception:
            pass

recuperar_banco_raiz_se_existir()


# --- CONTROLE DE SESSÃO E IDENTIFICAÇÃO DO USUÁRIO ---

@app.before_request
def carregar_empresa_logada():
    empresa_id = session.get('empresa_id')
    if empresa_id:
        g.empresa = db.session.get(Empresa, empresa_id)
        if not g.empresa:
            session.pop('empresa_id', None)
            g.empresa = None
    else:
        g.empresa = None


@app.after_request
def add_no_cache_headers(response):
    """
    Garante que LiteSpeed, proxies intermediários e navegadores NUNCA façam cache de páginas dinâmicas ou cookies.
    Isso é vital para evitar vazamento ou compartilhamento de telas/sessões entre diferentes computadores na hospedagem.
    """
    if not request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0, private'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        response.headers['X-LiteSpeed-Cache-Control'] = 'no-cache'
    return response


@app.context_processor
def inject_empresa():
    return dict(empresa=getattr(g, 'empresa', None), hoje=date.today())


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not getattr(g, 'empresa', None):
            flash('Por favor, faça login para acessar o sistema.', 'warning')
            return redirect(url_for('login', next=request.path))

        # Verificação de Bloqueio por Pagamento / Assinatura (exceto para o Dono Master)
        if not g.empresa.is_admin:
            hoje = date.today()
            esta_bloqueada = (g.empresa.status_assinatura != 'ATIVO') or (g.empresa.data_validade and g.empresa.data_validade < hoje)
            if esta_bloqueada:
                return redirect(url_for('assinatura_bloqueada'))

        return view_func(*args, **kwargs)
    return wrapped_view


def admin_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not getattr(g, 'empresa', None) or not g.empresa.is_admin:
            flash('Acesso restrito ao Administrador Master.', 'error')
            return redirect(url_for('dashboard'))
        return view_func(*args, **kwargs)
    return wrapped_view


# --- FILTROS DE FORMATAÇÃO (JINJA2) ---

@app.template_filter('moeda')
def formato_moeda(valor):
    try:
        val = float(valor or 0.0)
        return f"{val:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except (ValueError, TypeError):
        return "0,00"

@app.template_filter('data_br')
def formato_data(data_obj):
    if not data_obj:
        return "-"
    return data_obj.strftime('%d/%m/%Y')

def limpar_telefone_wa(telefone):
    if not telefone:
        return ""
    nums = re.sub(r'\D', '', str(telefone))
    if len(nums) in (10, 11):
        nums = '55' + nums
    return nums

@app.template_filter('fone_whatsapp')
def fone_whatsapp_filter(telefone):
    return limpar_telefone_wa(telefone)

@app.template_filter('msg_whatsapp_os')
def msg_whatsapp_os_filter(os_obj):
    cliente_nome = os_obj.cliente.nome.strip() if os_obj.cliente else "Cliente"
    item_info = f"{os_obj.veiculo.modelo} ({os_obj.veiculo.placa})" if os_obj.veiculo else "item / serviço"
    empresa = os_obj.empresa or getattr(g, 'empresa', None)
    nome_empresa = empresa.nome_empresa if empresa else "Empresa"
    
    linhas = [
        f"📋 *{nome_empresa.upper()}*",
        f"Olá, *{cliente_nome}*! 👋\n",
        f"Sua Ordem de Serviço *#{os_obj.id:04d}* foi finalizada e seu atendimento referente a *{item_info}* está concluído! ✅\n",
        "📋 *RESUMO DO ATENDIMENTO:*"
    ]
    
    pecas = [i for i in os_obj.itens if i.tipo == 'PECA']
    if pecas:
        linhas.append("\n📦 *Produtos / Peças / Materiais:*")
        for p in pecas:
            qtd_str = f"{p.quantidade:g}"
            preco_unit = formato_moeda(p.valor_unitario)
            sub_total = formato_moeda(p.subtotal)
            linhas.append(f"• {p.descricao} ({qtd_str}x R$ {preco_unit}) = R$ {sub_total}")
        linhas.append(f"  *Subtotal Materiais:* R$ {formato_moeda(os_obj.valor_pecas)}")
        
    servicos = [i for i in os_obj.itens if i.tipo == 'SERVICO']
    if servicos:
        linhas.append("\n🛠️ *Mão de Obra / Serviços Executados:*")
        for s in servicos:
            sub_total = formato_moeda(s.subtotal)
            linhas.append(f"• {s.descricao} = R$ {sub_total}")
        linhas.append(f"  *Subtotal Serviços:* R$ {formato_moeda(os_obj.valor_mao_obra)}")
        
    if os_obj.servico_executado:
        linhas.append(f"\n📝 *Observações / Laudo Técnico:*\n{os_obj.servico_executado}")
        
    linhas.append("\n" + ("─" * 22))
    linhas.append(f"💰 *VALOR TOTAL: R$ {formato_moeda(os_obj.valor_total)}*")
    if os_obj.forma_pagamento:
        linhas.append(f"💳 *Forma de Pagamento:* {os_obj.forma_pagamento}")
    if os_obj.retorno_previsto:
        linhas.append(f"📅 *Próxima Revisão Preventiva Sugerida:* {os_obj.retorno_previsto.strftime('%d/%m/%Y')}")
    linhas.append("🛡️ *Garantia:* 90 dias sobre serviços e materiais aplicados.")
    if os_obj.codigo_rastreio:
        linhas.append(f"📲 *Acompanhe seu atendimento online:* https://nandocozac.shop/status/{os_obj.codigo_rastreio}")
    if empresa and empresa.endereco:
        loc = empresa.endereco
        if empresa.cidade_uf:
            loc += f" - {empresa.cidade_uf}"
        linhas.append(f"\n📍 *Endereço:* {loc}")
    if empresa and (empresa.telefone or empresa.whatsapp):
        linhas.append(f"📞 *Contato:* {empresa.whatsapp or empresa.telefone}")
    linhas.append("Aguardamos você para a retirada / entrega. Qualquer dúvida estamos à disposição! 🤝")
    
    return "\n".join(linhas)


# --- AUTENTICAÇÃO (LOGIN, CADASTRO, LOGOUT) ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if getattr(g, 'empresa', None):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '').strip()

        empresa = Empresa.query.filter_by(email=email).first()
        if empresa and check_password_hash(empresa.senha_hash, senha):
            session['empresa_id'] = empresa.id
            
            # Se for empresa comum e estiver pendente ou bloqueada, informa com clareza
            if not empresa.is_admin:
                hoje = date.today()
                esta_bloqueada = (empresa.status_assinatura != 'ATIVO') or (empresa.data_validade and empresa.data_validade < hoje)
                if esta_bloqueada:
                    if empresa.status_assinatura == 'PENDENTE':
                        flash(f"Conta encontrada! Seu cadastro está concluído e aguarda confirmação de pagamento para liberar o acesso.", "info")
                    else:
                        flash("Sua assinatura está expirada ou bloqueada. Efetue o pagamento para renovar o acesso.", "warning")
                    return redirect(url_for('assinatura_bloqueada'))

            flash(f"Bem-vindo(a), {empresa.nome_empresa}!", "success")
            next_url = request.args.get('next')
            return redirect(next_url or url_for('dashboard'))
        else:
            flash("E-mail ou senha incorretos. Tente novamente.", "error")

    return render_template('login.html')


@app.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    if getattr(g, 'empresa', None):
        return redirect(url_for('dashboard'))

    master_empresa = Empresa.query.filter_by(is_admin=True).first()
    valor_mensal = (master_empresa.valor_mensalidade or 29.90) if master_empresa else 29.90
    valor_anual = (master_empresa.valor_anual or 249.90) if master_empresa else 249.90

    if request.method == 'POST':
        nome_empresa = request.form.get('nome_empresa', '').strip()
        email = request.form.get('email', '').strip().lower()
        telefone = request.form.get('telefone', '').strip()
        senha = request.form.get('senha', '').strip()
        confirmar_senha = request.form.get('confirmar_senha', '').strip()
        opcao_plano = request.form.get('opcao_plano', 'TRIAL').strip().upper()

        if opcao_plano not in ['TRIAL', 'MENSAL', 'ANUAL']:
            opcao_plano = 'TRIAL'

        if not nome_empresa or not email or not senha:
            flash("Preencha todos os campos obrigatórios.", "error")
            return render_template('cadastro.html', valor_mensal=valor_mensal, valor_anual=valor_anual)

        if senha != confirmar_senha:
            flash("As senhas não coincidem.", "error")
            return render_template('cadastro.html', valor_mensal=valor_mensal, valor_anual=valor_anual)

        if len(senha) < 6:
            flash("A senha deve conter no mínimo 6 caracteres.", "error")
            return render_template('cadastro.html', valor_mensal=valor_mensal, valor_anual=valor_anual)

        if Empresa.query.filter_by(email=email).first():
            flash("Este e-mail já está cadastrado. Faça login ou use outro e-mail.", "error")
            return render_template('cadastro.html', valor_mensal=valor_mensal, valor_anual=valor_anual)

        tipo_negocio = request.form.get('tipo_negocio', 'OFICINA').strip().upper()
        if tipo_negocio not in ['OFICINA', 'LOJA']:
            tipo_negocio = 'OFICINA'

        if opcao_plano == 'TRIAL':
            data_expiracao_teste = date.today() + timedelta(days=7)
            nova_empresa = Empresa(
                nome_empresa=nome_empresa,
                email=email,
                telefone=telefone,
                whatsapp=telefone,
                senha_hash=generate_password_hash(senha),
                is_admin=False,
                tipo_negocio=tipo_negocio,
                status_assinatura="ATIVO",
                data_validade=data_expiracao_teste,
                observacoes_admin="Período de Teste Grátis (7 dias)",
                logo_filename=None,
                logo_base64=None,
                mensagem_rodape="Agradecemos a preferência! Volte sempre."
            )
            db.session.add(nova_empresa)
            db.session.commit()

            session['empresa_id'] = nova_empresa.id
            flash(f"🎉 Bem-vindo! Sua empresa '{nova_empresa.nome_empresa}' foi cadastrada com sucesso. Seu teste grátis de 7 dias com acesso total está ativo até {data_expiracao_teste.strftime('%d/%m/%Y')}!", "success")
            return redirect(url_for('dashboard'))

        else:
            # Cliente optou por assinar diretamente no plano MENSAL ou ANUAL
            nova_empresa = Empresa(
                nome_empresa=nome_empresa,
                email=email,
                telefone=telefone,
                whatsapp=telefone,
                senha_hash=generate_password_hash(senha),
                is_admin=False,
                tipo_negocio=tipo_negocio,
                status_assinatura="PENDENTE",
                data_validade=None,
                observacoes_admin=f"Plano selecionado no cadastro: {opcao_plano}",
                logo_filename=None,
                logo_base64=None,
                mensagem_rodape="Agradecemos a preferência! Volte sempre."
            )
            db.session.add(nova_empresa)
            db.session.commit()

            session['empresa_id'] = nova_empresa.id
            nome_amigavel = "Mensal" if opcao_plano == 'MENSAL' else "Anual"
            flash(f"🎉 Conta criada com sucesso! Conclua o pagamento do seu Plano {nome_amigavel} abaixo para liberação imediata via Mercado Pago.", "success")
            return redirect(url_for('assinatura_bloqueada', plano=opcao_plano))

    return render_template('cadastro.html', valor_mensal=valor_mensal, valor_anual=valor_anual)


@app.route('/assinatura/ativar-trial', methods=['POST'])
def assinatura_ativar_trial():
    """Permite que uma empresa com cadastro pendente ative seus 7 dias grátis caso prefira testar antes de pagar"""
    if not getattr(g, 'empresa', None):
        return redirect(url_for('login'))

    if 'Teste Grátis' in (g.empresa.observacoes_admin or ''):
        flash("Seu período de teste grátis de 7 dias já foi utilizado anteriormente.", "warning")
        return redirect(url_for('assinatura_bloqueada'))

    hoje = date.today()
    g.empresa.status_assinatura = "ATIVO"
    g.empresa.data_validade = hoje + timedelta(days=7)
    obs = g.empresa.observacoes_admin or ""
    g.empresa.observacoes_admin = (obs + " | Período de Teste Grátis (7 dias)").strip(' |')
    db.session.commit()

    flash(f"🎉 Teste grátis de 7 dias ativado com sucesso! Aproveite todos os recursos do painel até {g.empresa.data_validade.strftime('%d/%m/%Y')}.", "success")
    return redirect(url_for('dashboard'))


@app.route('/logout')
def logout():
    session.pop('empresa_id', None)
    flash("Sua sessão foi encerrada com segurança.", "info")
    return redirect(url_for('login'))


# --- TELA DE BLOQUEIO POR FALTA DE PAGAMENTO / PLANOS ---

@app.route('/assinatura/planos')
@app.route('/assinatura/bloqueada')
def assinatura_bloqueada():
    if not getattr(g, 'empresa', None):
        return redirect(url_for('login'))

    # Se for o Master Admin, nunca é bloqueado
    if g.empresa.is_admin:
        return redirect(url_for('dashboard'))

    hoje = date.today()
    esta_bloqueada = (g.empresa.status_assinatura != 'ATIVO') or (g.empresa.data_validade and g.empresa.data_validade < hoje)
    quer_upgrade = (request.args.get('upgrade', '') == '1') or (request.path == '/assinatura/planos')
    if not esta_bloqueada and not quer_upgrade:
        return redirect(url_for('dashboard'))

    master_empresa = Empresa.query.filter_by(is_admin=True).first()
    return render_template('bloqueado.html', empresa=g.empresa, master_empresa=master_empresa, quer_upgrade=quer_upgrade)


@app.route('/assinatura/checkout-mercadopago', methods=['POST'])
def assinatura_checkout_mercadopago():
    """Gera link de pagamento do Mercado Pago (Pix, Cartão, Boleto) para a assinatura"""
    if not getattr(g, 'empresa', None):
        return redirect(url_for('login'))

    plano = request.form.get('plano', 'MENSAL').upper() # 'MENSAL' ou 'ANUAL'
    tipo_plano = request.form.get('tipo_plano', 'BASICO').upper() # 'BASICO' ou 'FISCAL'

    resultado = criar_preferencia_checkout(
        empresa_compradora=g.empresa,
        plano=plano,
        tipo_plano=tipo_plano,
        base_url=request.host_url
    )

    if resultado.get('success') and resultado.get('init_point'):
        return redirect(resultado['init_point'])
    else:
        erro = resultado.get('error', 'Falha ao conectar com o gateway do Mercado Pago.')
        flash(f"Não foi possível iniciar o pagamento no Mercado Pago: {erro}", "error")
        return redirect(url_for('assinatura_bloqueada'))


@app.route('/assinatura/retorno')
def assinatura_retorno():
    """Retorno do cliente após realizar ou tentar o pagamento no Mercado Pago"""
    if not getattr(g, 'empresa', None):
        return redirect(url_for('login'))

    collection_status = request.args.get('collection_status') or request.args.get('status')
    external_reference = request.args.get('external_reference')
    payment_id = request.args.get('payment_id') or request.args.get('collection_id')

    # Caso status venha aprovado no redirect
    if collection_status == 'approved' and external_reference:
        sucesso, emp, msg = ativar_assinatura_por_referencia(external_reference, payment_id)
        if sucesso:
            flash(f"🎉 Pagamento aprovado! {msg}", "success")
            return redirect(url_for('dashboard'))

    # Caso haja payment_id, consulta diretamente na API do Mercado Pago para conferir se foi aprovado
    if payment_id:
        payment_info = consultar_pagamento(payment_id)
        if payment_info and payment_info.get('status') == 'approved':
            ext_ref = payment_info.get('external_reference') or external_reference
            sucesso, emp, msg = ativar_assinatura_por_referencia(ext_ref, payment_id)
            if sucesso:
                flash(f"🎉 Pagamento confirmado pelo Mercado Pago! {msg}", "success")
                return redirect(url_for('dashboard'))

    if collection_status == 'pending':
        flash("⏳ Seu pagamento via Pix/Boleto está sendo processado. Assim que confirmado (geralmente poucos segundos para Pix), seu acesso será liberado automaticamente!", "info")
    else:
        flash("O pagamento ainda não foi concluído ou está em processamento.", "warning")

    return redirect(url_for('assinatura_bloqueada'))


@app.route('/webhook/mercadopago', methods=['GET', 'POST'])
def webhook_mercadopago():
    """Webhook do Mercado Pago para liberação automática e instantânea de assinaturas"""
    payment_id = None

    if request.is_json:
        data = request.get_json(silent=True) or {}
        inner_data = data.get('data') or {}
        if isinstance(inner_data, dict):
            payment_id = inner_data.get('id')
        if not payment_id:
            payment_id = data.get('id')

    if not payment_id:
        payment_id = request.args.get('data.id') or request.args.get('id')

    if payment_id:
        payment_info = consultar_pagamento(payment_id)
        if payment_info and payment_info.get('status') == 'approved':
            external_ref = payment_info.get('external_reference')
            if external_ref:
                sucesso, emp, msg = ativar_assinatura_por_referencia(external_ref, payment_id)
                if sucesso:
                    print(f"[Webhook Mercado Pago] {msg} (Payment ID: {payment_id})")

    return jsonify({"status": "received"}), 200


# --- PAINEL DO ADMINISTRADOR MASTER (DONO DA PLATAFORMA) ---

@app.route('/admin/empresas')
@login_required
@admin_required
def admin_empresas():
    empresas = Empresa.query.order_by(Empresa.id.desc()).all()
    db_exists = os.path.exists(DB_PATH)
    db_size_kb = round(os.path.getsize(DB_PATH) / 1024, 1) if db_exists else 0
    db_mtime = time.strftime('%d/%m/%Y %H:%M:%S', time.localtime(os.path.getmtime(DB_PATH))) if db_exists else 'N/A'
    total_clientes = Cliente.query.count()
    total_os = OrdemServico.query.count()
    return render_template(
        'admin_empresas.html',
        empresas=empresas,
        hoje=date.today(),
        db_path=DB_PATH,
        db_size_kb=db_size_kb,
        db_mtime=db_mtime,
        total_clientes=total_clientes,
        total_os=total_os
    )


@app.route('/admin/empresa/nova', methods=['POST'])
@login_required
@admin_required
def admin_nova_empresa():
    nome = request.form.get('nome_empresa', '').strip()
    email = request.form.get('email', '').strip().lower()
    telefone = request.form.get('telefone', '').strip()
    senha = request.form.get('senha', '').strip()
    status = request.form.get('status', 'ATIVO').strip()
    dias_validade = int(request.form.get('dias_validade', 30))

    if not nome or not email or not senha:
        flash("Nome, e-mail e senha são obrigatórios.", "error")
        return redirect(url_for('admin_empresas'))

    if Empresa.query.filter_by(email=email).first():
        flash("Este e-mail já está cadastrado em outra empresa.", "error")
        return redirect(url_for('admin_empresas'))

    hoje = date.today()
    tipo_negocio = request.form.get('tipo_negocio', 'OFICINA').strip().upper()
    if tipo_negocio not in ['OFICINA', 'LOJA']:
        tipo_negocio = 'OFICINA'

    nova_emp = Empresa(
        nome_empresa=nome,
        email=email,
        telefone=telefone,
        whatsapp=telefone,
        senha_hash=generate_password_hash(senha),
        is_admin=False,
        tipo_negocio=tipo_negocio,
        status_assinatura=status,
        data_validade=hoje + timedelta(days=dias_validade) if status == 'ATIVO' else None,
        logo_filename=None,
        logo_base64=None,
        mensagem_rodape="Agradecemos a preferência! Volte sempre."
    )
    db.session.add(nova_emp)
    db.session.commit()

    flash(f"Empresa '{nome}' cadastrada com sucesso com status '{status}'!", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/cobranca', methods=['POST'])
@login_required
@admin_required
def admin_salvar_cobranca():
    g.empresa.chave_pix = request.form.get('chave_pix', '').strip()
    g.empresa.titular_pix = request.form.get('titular_pix', '').strip()
    def limpar_valor(val_raw, default_val):
        if not val_raw:
            return default_val
        val_str = str(val_raw).replace('R$', '').replace(' ', '').replace(',', '.')
        try:
            return round(float(val_str), 2)
        except (ValueError, TypeError):
            return default_val

    g.empresa.valor_mensalidade = limpar_valor(request.form.get('valor_mensalidade'), g.empresa.valor_mensalidade or 29.90)
    g.empresa.valor_anual = limpar_valor(request.form.get('valor_anual'), g.empresa.valor_anual or 249.90)
    g.empresa.valor_mensalidade_fiscal = limpar_valor(request.form.get('valor_mensalidade_fiscal'), g.empresa.valor_mensalidade_fiscal or 79.90)
    g.empresa.valor_anual_fiscal = limpar_valor(request.form.get('valor_anual_fiscal'), g.empresa.valor_anual_fiscal or 699.90)

    g.empresa.mercadopago_access_token = request.form.get('mercadopago_access_token', '').strip()
    g.empresa.mercadopago_public_key = request.form.get('mercadopago_public_key', '').strip()

    db.session.commit()
    flash("Dados de cobrança e valores atualizados com sucesso!", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/alternar-fiscal', methods=['POST'])
@login_required
@admin_required
def admin_alternar_fiscal(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    emp.permite_emissao_fiscal = not emp.permite_emissao_fiscal
    db.session.commit()

    if emp.permite_emissao_fiscal:
        flash(f"Módulo Fiscal LIBERADO com sucesso para a empresa '{emp.nome_empresa}'!", "success")
    else:
        flash(f"Módulo Fiscal BLOQUEADO para a empresa '{emp.nome_empresa}'.", "info")

    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/renovar', methods=['POST'])
@login_required
@admin_required
def admin_renovar_empresa(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    hoje = date.today()
    base_data = emp.data_validade if (emp.data_validade and emp.data_validade >= hoje) else hoje
    emp.data_validade = base_data + timedelta(days=30)
    emp.status_assinatura = 'ATIVO'
    db.session.commit()

    flash(f"Acesso da empresa '{emp.nome_empresa}' estendido por +30 dias (Válido até {emp.data_validade.strftime('%d/%m/%Y')})!", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/renovar-anual', methods=['POST'])
@login_required
@admin_required
def admin_renovar_empresa_anual(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    hoje = date.today()
    base_data = emp.data_validade if (emp.data_validade and emp.data_validade >= hoje) else hoje
    emp.data_validade = base_data + timedelta(days=365)
    emp.status_assinatura = 'ATIVO'
    db.session.commit()

    flash(f"Acesso ANUAL da empresa '{emp.nome_empresa}' estendido por +1 ano (Válido até {emp.data_validade.strftime('%d/%m/%Y')})!", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/ativar', methods=['POST'])
@login_required
@admin_required
def admin_ativar_empresa(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    hoje = date.today()
    emp.status_assinatura = 'ATIVO'
    if not emp.data_validade or emp.data_validade < hoje:
        emp.data_validade = hoje + timedelta(days=30)

    db.session.commit()
    flash(f"Acesso da empresa '{emp.nome_empresa}' liberado com sucesso!", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/bloquear', methods=['POST'])
@login_required
@admin_required
def admin_bloquear_empresa(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    emp.status_assinatura = 'BLOQUEADO'
    db.session.commit()
    flash(f"Acesso da empresa '{emp.nome_empresa}' foi bloqueado.", "warning")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/empresa/<int:empresa_id>/excluir', methods=['POST'])
@login_required
@admin_required
def admin_excluir_empresa(empresa_id):
    emp = db.session.get(Empresa, empresa_id)
    if not emp:
        flash("Empresa não encontrada.", "error")
        return redirect(url_for('admin_empresas'))

    if emp.is_admin or emp.id == g.empresa.id:
        flash("Não é permitido excluir a conta Master Admin principal!", "error")
        return redirect(url_for('admin_empresas'))

    nome = emp.nome_empresa

    # 1. Excluir transações financeiras da empresa
    Transacao.query.filter_by(empresa_id=emp.id).delete()

    # 2. Excluir ordens de serviço (e seus itens de OS associados)
    ordens = OrdemServico.query.filter_by(empresa_id=emp.id).all()
    for os_item in ordens:
        ItemOS.query.filter_by(os_id=os_item.id).delete()
        db.session.delete(os_item)

    # 3. Excluir veículos e clientes da empresa
    clientes = Cliente.query.filter_by(empresa_id=emp.id).all()
    for c in clientes:
        Veiculo.query.filter_by(cliente_id=c.id).delete()
        db.session.delete(c)

    # 4. Excluir a própria empresa
    db.session.delete(emp)
    db.session.commit()

    flash(f"A empresa '{nome}' e todos os seus registros foram excluídos com sucesso.", "success")
    return redirect(url_for('admin_empresas'))


@app.route('/admin/banco/backup')
@login_required
@admin_required
def admin_backup_banco():
    if not os.path.exists(DB_PATH):
        flash("Arquivo de banco de dados não encontrado.", "error")
        return redirect(url_for('admin_empresas'))

    nome_arquivo = f"backup_painel_gestao_{date.today().strftime('%Y%m%d')}_{int(time.time())}.db"
    return send_file(DB_PATH, as_attachment=True, download_name=nome_arquivo)


@app.route('/admin/banco/restaurar', methods=['POST'])
@login_required
@admin_required
def admin_restaurar_banco():
    arquivo = request.files.get('arquivo_banco')
    if not arquivo or not arquivo.filename:
        flash("Nenhum arquivo de banco de dados foi selecionado.", "error")
        return redirect(url_for('admin_empresas'))

    if not arquivo.filename.lower().endswith(('.db', '.sqlite', '.sqlite3')):
        flash("Formato de arquivo inválido. Por favor, envie um arquivo com extensão .db ou .sqlite.", "error")
        return redirect(url_for('admin_empresas'))

    conteudo = arquivo.read()
    # Verifica assinatura do SQLite
    if not conteudo.startswith(b'SQLite format 3\x00'):
        flash("O arquivo enviado não é um banco de dados SQLite válido!", "error")
        return redirect(url_for('admin_empresas'))

    try:
        # 1. Faz backup automático do banco atual antes de sobrescrever
        if os.path.exists(DB_PATH):
            backup_emergencia = f"{DB_PATH}.seguranca_{int(time.time())}"
            shutil.copy2(DB_PATH, backup_emergencia)

        # 2. Fecha conexões ativas do SQLAlchemy
        db.session.remove()
        db.engine.dispose()

        # 3. Grava o novo banco
        with open(DB_PATH, 'wb') as f:
            f.write(conteudo)

        # 4. Roda as migrações automáticas para garantir todas as tabelas e colunas
        migrar_banco_multiempresa()

        flash("Banco de dados restaurado com sucesso! Os novos dados já estão em vigor.", "success")
    except Exception as e:
        flash(f"Erro ao restaurar banco de dados: {str(e)}", "error")

    return redirect(url_for('admin_empresas'))


# --- PÁGINA INICIAL / LANDING PAGE DE ALTA CONVERSÃO ---

@app.route('/')
def index():
    if getattr(g, 'empresa', None):
        return redirect(url_for('dashboard'))
    return render_template('landing.html')


@app.route('/solicite-proposta', methods=['GET', 'POST'])
@app.route('/proposta', methods=['GET', 'POST'])
def landing_proposta():
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        nome_empresa = request.form.get('nome_empresa', '').strip()
        email = request.form.get('email', '').strip()
        telefone = request.form.get('telefone', '').strip()
        tipo_negocio = request.form.get('tipo_negocio', 'OFICINA').strip()
        # Redireciona com preenchimento automático para o cadastro da empresa
        return redirect(url_for('cadastro', nome_empresa=nome_empresa, email=email, telefone=telefone, tipo_negocio=tipo_negocio))
    return render_template('landing.html')


# --- DASHBOARD ISOLADO POR EMPRESA ---

@app.route('/dashboard')
@login_required
def dashboard():
    hoje = date.today()
    empresa_id = g.empresa.id

    transacoes_hoje = Transacao.query.filter_by(empresa_id=empresa_id, data_movimento=hoje).all()
    receitas_hoje = sum(t.valor for t in transacoes_hoje if t.tipo == 'RECEITA')
    despesas_hoje = sum(t.valor for t in transacoes_hoje if t.tipo == 'DESPESA')
    saldo_hoje = receitas_hoje - despesas_hoje

    primeiro_dia_mes = hoje.replace(day=1)
    transacoes_mes = Transacao.query.filter(
        Transacao.empresa_id == empresa_id,
        Transacao.data_movimento >= primeiro_dia_mes
    ).all()
    receitas_mes = sum(t.valor for t in transacoes_mes if t.tipo == 'RECEITA')
    despesas_mes = sum(t.valor for t in transacoes_mes if t.tipo == 'DESPESA')
    faturamento_mes = receitas_mes - despesas_mes

    os_abertas = OrdemServico.query.filter_by(empresa_id=empresa_id, status='ABERTA').order_by(OrdemServico.id.desc()).all()

    return render_template(
        'dashboard.html',
        receitas_hoje=receitas_hoje,
        despesas_hoje=despesas_hoje,
        saldo_hoje=saldo_hoje,
        receitas_mes=receitas_mes,
        despesas_mes=despesas_mes,
        faturamento_mes=faturamento_mes,
        os_abertas=os_abertas
    )


# --- LANÇAMENTO RÁPIDO DE DESPESA ---

@app.route('/financeiro/despesa', methods=['POST'])
@login_required
def nova_despesa():
    descricao = request.form.get('descricao', '').strip()
    forma_pagamento = request.form.get('forma_pagamento', 'Dinheiro').strip()
    raw_valor = request.form.get('valor', '0').replace('.', '').replace(',', '.').strip()

    try:
        valor = float(raw_valor)
    except (ValueError, TypeError):
        valor = 0.0

    if descricao and valor > 0:
        nova = Transacao(
            empresa_id=g.empresa.id,
            tipo='DESPESA',
            descricao=descricao,
            valor=round(valor, 2),
            forma_pagamento=forma_pagamento,
            data_movimento=date.today()
        )
        db.session.add(nova)
        db.session.commit()

    return redirect(url_for('dashboard'))


# --- ORDENS DE SERVIÇO ---

@app.route('/os')
@login_required
def lista_os():
    status = request.args.get('status', '').strip()
    busca = request.args.get('busca', '').strip()

    query = OrdemServico.query.filter_by(empresa_id=g.empresa.id)

    if status:
        query = query.filter_by(status=status)

    if busca:
        busca_num = busca.lstrip('#').strip()
        filtro_busca = [
            Cliente.nome.ilike(f'%{busca}%'),
            Cliente.telefone.ilike(f'%{busca}%'),
            Veiculo.placa.ilike(f'%{busca}%'),
            Veiculo.modelo.ilike(f'%{busca}%')
        ]
        if busca_num.isdigit():
            filtro_busca.append(OrdemServico.numero_sequencial == int(busca_num))
            filtro_busca.append(OrdemServico.id == int(busca_num))

        query = query.join(Cliente).outerjoin(Veiculo).filter(or_(*filtro_busca))

    todas_os = query.order_by(OrdemServico.id.desc()).all()
    return render_template('os_lista.html', todas_os=todas_os, status_atual=status, busca=busca)


def converter_valor_monetario(v):
    if not v:
        return 0.0
    try:
        s = str(v).strip().replace('R$', '').replace(' ', '')
        if ',' in s and '.' in s:
            s = s.replace('.', '').replace(',', '.')
        elif ',' in s:
            s = s.replace(',', '.')
        return max(0.0, float(s))
    except (ValueError, TypeError):
        return 0.0


@app.route('/os/nova', methods=['GET', 'POST'])
@login_required
def nova_os():
    if request.method == 'POST':
        cliente_id_form = request.form.get('cliente_id', type=int)
        nome = request.form.get('cliente_nome', '').strip()
        telefone = request.form.get('cliente_telefone', '').strip()
        placa = request.form.get('veiculo_placa', '').strip().upper()
        modelo = request.form.get('veiculo_modelo', '').strip()
        problema = request.form.get('problema', '').strip()

        cpf_cnpj = request.form.get('cliente_cpf_cnpj', '').strip()
        email = request.form.get('cliente_email', '').strip().lower()

        # Dados de Vendedor e Mão de Obra / Mecânico
        vendedor_id = request.form.get('vendedor_id', type=int)
        mecanico_id = request.form.get('mecanico_id', type=int)
        porc_vendedor_input = request.form.get('porcentagem_comissao_vendedor', '').strip()
        porc_mecanico_input = request.form.get('porcentagem_comissao_mecanico', '').strip()

        porc_vendedor = converter_valor_monetario(porc_vendedor_input)
        porc_mecanico = converter_valor_monetario(porc_mecanico_input)

        if vendedor_id:
            v_obj = Colaborador.query.filter_by(id=vendedor_id, empresa_id=g.empresa.id).first()
            if v_obj and not porc_vendedor_input:
                porc_vendedor = v_obj.porcentagem_padrao
        else:
            vendedor_id = None
            porc_vendedor = 0.0

        if mecanico_id:
            m_obj = Colaborador.query.filter_by(id=mecanico_id, empresa_id=g.empresa.id).first()
            if m_obj and not porc_mecanico_input:
                porc_mecanico = m_obj.porcentagem_padrao
        else:
            mecanico_id = None
            porc_mecanico = 0.0

        cliente = None
        if cliente_id_form:
            cliente = Cliente.query.filter_by(id=cliente_id_form, empresa_id=g.empresa.id).first()

        if not cliente and cpf_cnpj:
            cliente = Cliente.query.filter_by(empresa_id=g.empresa.id, cpf_cnpj=cpf_cnpj).first()

        if not cliente and telefone:
            cliente = Cliente.query.filter_by(empresa_id=g.empresa.id, telefone=telefone).first()

        if not cliente:
            cliente = Cliente(empresa_id=g.empresa.id, nome=nome, telefone=telefone, cpf_cnpj=cpf_cnpj, email=email)
            db.session.add(cliente)
            db.session.flush()
        else:
            if nome and cliente.nome != nome:
                cliente.nome = nome
            if telefone and not cliente.telefone:
                cliente.telefone = telefone
            if cpf_cnpj and not cliente.cpf_cnpj:
                cliente.cpf_cnpj = cpf_cnpj
            if email and not cliente.email:
                cliente.email = email

        veiculo = None
        if placa:
            veiculo = Veiculo.query.join(Cliente).filter(
                Cliente.empresa_id == g.empresa.id,
                Veiculo.placa == placa
            ).first()
            if not veiculo:
                veiculo = Veiculo(cliente_id=cliente.id, placa=placa, modelo=modelo)
                db.session.add(veiculo)
                db.session.flush()

        # Obter próximo número sequencial para a empresa atual (iniciando em 1)
        ultima_os = OrdemServico.query.filter_by(empresa_id=g.empresa.id).order_by(OrdemServico.id.desc()).first()
        if ultima_os and ultima_os.numero_sequencial:
            proximo_seq = ultima_os.numero_sequencial + 1
        else:
            qtd_existente = OrdemServico.query.filter_by(empresa_id=g.empresa.id).count()
            proximo_seq = qtd_existente + 1

        os_nova = OrdemServico(
            empresa_id=g.empresa.id,
            numero_sequencial=proximo_seq,
            cliente_id=cliente.id,
            veiculo_id=veiculo.id if veiculo else None,
            vendedor_id=vendedor_id,
            mecanico_id=mecanico_id,
            porcentagem_comissao_vendedor=porc_vendedor,
            porcentagem_comissao_mecanico=porc_mecanico,
            descricao_problema=problema,
            status='ABERTA',
            etapa_andamento='RECEBIDO',
            codigo_rastreio=uuid.uuid4().hex[:12],
            data_abertura=date.today()
        )
        db.session.add(os_nova)
        db.session.commit()

        return redirect(url_for('ver_os', os_id=os_nova.id))

    cliente_id_arg = request.args.get('cliente_id', type=int)
    cliente_pre = None
    if cliente_id_arg:
        cliente_pre = Cliente.query.filter_by(id=cliente_id_arg, empresa_id=g.empresa.id).first()

    colaboradores = Colaborador.query.filter_by(empresa_id=g.empresa.id, ativo=True).order_by(Colaborador.nome).all()
    vendedores = [c for c in colaboradores if c.funcao == 'VENDEDOR']
    mecanicos = [c for c in colaboradores if c.funcao in ['MECANICO', 'TECNICO']]

    return render_template('os_form.html', cliente_pre=cliente_pre, colaboradores=colaboradores, vendedores=vendedores, mecanicos=mecanicos)


@app.route('/os/<int:os_id>')
@login_required
def ver_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()
    colaboradores = Colaborador.query.filter_by(empresa_id=g.empresa.id, ativo=True).order_by(Colaborador.nome).all()
    vendedores = [c for c in colaboradores if c.funcao == 'VENDEDOR']
    mecanicos = [c for c in colaboradores if c.funcao in ['MECANICO', 'TECNICO']]
    return render_template('os_print.html', os=os, colaboradores=colaboradores, vendedores=vendedores, mecanicos=mecanicos)


@app.route('/os/<int:os_id>/pdf')
def ver_os_pdf(os_id):
    # O PDF pode ser acessado pelo cliente via WhatsApp ou impresso pela empresa
    os = OrdemServico.query.get_or_404(os_id)
    empresa = os.empresa
    return render_template('os_cliente_pdf.html', os=os, empresa=empresa)


@app.route('/os/<int:os_id>/concluir', methods=['POST'])
@login_required
def concluir_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()

    if os.status == 'CONCLUIDA':
        return redirect(url_for('ver_os', os_id=os.id))

    def converter_valor(v):
        try:
            return max(0.0, float(str(v or '0').replace('.', '').replace(',', '.').strip()))
        except (ValueError, TypeError):
            return 0.0

    try:
        # 1. Processar Peças / Materiais
        peca_nomes = request.form.getlist('peca_nome[]')
        peca_qtds = request.form.getlist('peca_qtd[]')
        peca_valores = request.form.getlist('peca_valor[]')
        peca_prod_ids = request.form.getlist('peca_prod_id[]')

        total_pecas = 0.0
        for i, (nome, qtd_str, val_str) in enumerate(zip(peca_nomes, peca_qtds, peca_valores)):
            nome_clean = nome.strip()
            qtd = converter_valor(qtd_str) or 1.0
            val_unit = converter_valor(val_str)
            sub = round(qtd * val_unit, 2)
            if nome_clean:
                item = ItemOS(
                    os_id=os.id,
                    tipo='PECA',
                    descricao=nome_clean,
                    quantidade=qtd,
                    valor_unitario=val_unit,
                    subtotal=sub
                )
                # Vínculo com produto do catálogo e baixa no estoque
                if i < len(peca_prod_ids) and peca_prod_ids[i]:
                    try:
                        p_id = int(peca_prod_ids[i])
                        prod = Produto.query.filter_by(id=p_id, empresa_id=g.empresa.id).first()
                        if prod:
                            item.produto_id = prod.id
                            prod.estoque_atual = round(prod.estoque_atual - qtd, 2)
                            if not item.ncm and prod.ncm:
                                item.ncm = prod.ncm
                            if not item.cfop and prod.cfop:
                                item.cfop = prod.cfop
                    except (ValueError, TypeError):
                        pass

                db.session.add(item)
                total_pecas += sub

        # 2. Processar Serviços / Mão de Obra
        serv_nomes = request.form.getlist('servico_nome[]')
        serv_qtds = request.form.getlist('servico_qtd[]')
        serv_valores = request.form.getlist('servico_valor[]')
        serv_prod_ids = request.form.getlist('servico_prod_id[]')

        total_servicos = 0.0
        for i, (nome, qtd_str, val_str) in enumerate(zip(serv_nomes, serv_qtds, serv_valores)):
            nome_clean = nome.strip()
            qtd = converter_valor(qtd_str) or 1.0
            val_unit = converter_valor(val_str)
            sub = round(qtd * val_unit, 2)
            if nome_clean:
                item = ItemOS(
                    os_id=os.id,
                    tipo='SERVICO',
                    descricao=nome_clean,
                    quantidade=qtd,
                    valor_unitario=val_unit,
                    subtotal=sub
                )
                if i < len(serv_prod_ids) and serv_prod_ids[i]:
                    try:
                        s_id = int(serv_prod_ids[i])
                        serv_prod = Produto.query.filter_by(id=s_id, empresa_id=g.empresa.id).first()
                        if serv_prod:
                            item.produto_id = serv_prod.id
                            if not item.codigo_servico_municipal and serv_prod.codigo_servico_municipal:
                                item.codigo_servico_municipal = serv_prod.codigo_servico_municipal
                            if not item.aliquota_iss and serv_prod.aliquota_iss:
                                item.aliquota_iss = serv_prod.aliquota_iss
                    except (ValueError, TypeError):
                        pass

                db.session.add(item)
                total_servicos += sub

        # Suporte fallback caso os campos antigos de valor tenham sido preenchidos
        if total_pecas == 0:
            total_pecas = converter_valor(request.form.get('valor_pecas', '0'))
        if total_servicos == 0:
            total_servicos = converter_valor(request.form.get('valor_mao_obra', '0'))

        os.valor_pecas = round(total_pecas, 2)
        os.valor_mao_obra = round(total_servicos, 2)
        os.valor_total = round(total_pecas + total_servicos, 2)

        os.servico_executado = request.form.get('servico_executado', '').strip()
        os.forma_pagamento = request.form.get('forma_pagamento', 'Dinheiro').strip()
        os.status = 'CONCLUIDA'
        if os.etapa_andamento in ['RECEBIDO', 'DIAGNOSTICO', 'EM_EXECUCAO']:
            os.etapa_andamento = 'PRONTO'
        os.data_conclusao = date.today()

        # Configuração flexível da data de retorno preventivo
        opcao_retorno = request.form.get('opcao_retorno', '90').strip()
        data_retorno_manual = request.form.get('data_retorno_manual', '').strip()

        if data_retorno_manual:
            try:
                os.retorno_previsto = date.fromisoformat(data_retorno_manual)
            except (ValueError, TypeError):
                os.retorno_previsto = date.today() + timedelta(days=90)
        elif opcao_retorno == 'sem_retorno':
            os.retorno_previsto = None
        else:
            try:
                dias = int(opcao_retorno)
                os.retorno_previsto = date.today() + timedelta(days=dias)
            except (ValueError, TypeError):
                os.retorno_previsto = date.today() + timedelta(days=90)

        # Processamento e Cálculo das Comissões (Vendedor e Mecânico / Mão de Obra)
        vendedor_id_form = request.form.get('vendedor_id')
        mecanico_id_form = request.form.get('mecanico_id')

        vendedor_id = int(vendedor_id_form) if (vendedor_id_form and vendedor_id_form.strip().isdigit()) else os.vendedor_id
        mecanico_id = int(mecanico_id_form) if (mecanico_id_form and mecanico_id_form.strip().isdigit()) else os.mecanico_id

        porc_vendedor_input = request.form.get('porcentagem_comissao_vendedor', '').strip()
        porc_mecanico_input = request.form.get('porcentagem_comissao_mecanico', '').strip()

        if vendedor_id:
            vend = Colaborador.query.filter_by(id=vendedor_id, empresa_id=g.empresa.id).first()
            if vend:
                os.vendedor_id = vend.id
                os.porcentagem_comissao_vendedor = converter_valor_monetario(porc_vendedor_input) if porc_vendedor_input != '' else (os.porcentagem_comissao_vendedor or vend.porcentagem_padrao)
                base_vendedor = os.valor_pecas if vend.tipo_base == 'PECAS' else os.valor_total
                os.valor_comissao_vendedor = round(base_vendedor * (os.porcentagem_comissao_vendedor / 100.0), 2)
        else:
            os.vendedor_id = None
            os.porcentagem_comissao_vendedor = 0.0
            os.valor_comissao_vendedor = 0.0

        if mecanico_id:
            mec = Colaborador.query.filter_by(id=mecanico_id, empresa_id=g.empresa.id).first()
            if mec:
                os.mecanico_id = mec.id
                os.porcentagem_comissao_mecanico = converter_valor_monetario(porc_mecanico_input) if porc_mecanico_input != '' else (os.porcentagem_comissao_mecanico or mec.porcentagem_padrao)
                os.valor_comissao_mecanico = round(os.valor_mao_obra * (os.porcentagem_comissao_mecanico / 100.0), 2)
        else:
            os.mecanico_id = None
            os.porcentagem_comissao_mecanico = 0.0
            os.valor_comissao_mecanico = 0.0

        # Entrada no Livro Caixa com isolamento de empresa
        if os.valor_total > 0:
            caixa = Transacao(
                empresa_id=g.empresa.id,
                tipo='RECEITA',
                descricao=f"Recebimento OS #{os.id} - {os.cliente.nome}",
                valor=os.valor_total,
                forma_pagamento=os.forma_pagamento,
                data_movimento=date.today(),
                os_id=os.id
            )
            db.session.add(caixa)

        db.session.commit()

    except Exception:
        db.session.rollback()
        raise

    return redirect(url_for('ver_os', os_id=os.id))


@app.route('/os/<int:os_id>/atualizar-comissao', methods=['POST'])
@login_required
def atualizar_comissao_os(os_id):
    """Permite ao dono ajustar ou vincular vendedor e executor da mão de obra em qualquer OS já existente"""
    os_obj = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()

    vendedor_id_form = request.form.get('vendedor_id', '').strip()
    mecanico_id_form = request.form.get('mecanico_id', '').strip()
    porc_vendedor_input = request.form.get('porcentagem_comissao_vendedor', '').strip()
    porc_mecanico_input = request.form.get('porcentagem_comissao_mecanico', '').strip()

    if vendedor_id_form.isdigit():
        vend = Colaborador.query.filter_by(id=int(vendedor_id_form), empresa_id=g.empresa.id).first()
        if vend:
            os_obj.vendedor_id = vend.id
            os_obj.porcentagem_comissao_vendedor = converter_valor_monetario(porc_vendedor_input) if porc_vendedor_input != '' else vend.porcentagem_padrao
            base_vend = os_obj.valor_pecas if vend.tipo_base == 'PECAS' else os_obj.valor_total
            os_obj.valor_comissao_vendedor = round(base_vend * (os_obj.porcentagem_comissao_vendedor / 100.0), 2)
    else:
        os_obj.vendedor_id = None
        os_obj.porcentagem_comissao_vendedor = 0.0
        os_obj.valor_comissao_vendedor = 0.0

    if mecanico_id_form.isdigit():
        mec = Colaborador.query.filter_by(id=int(mecanico_id_form), empresa_id=g.empresa.id).first()
        if mec:
            os_obj.mecanico_id = mec.id
            os_obj.porcentagem_comissao_mecanico = converter_valor_monetario(porc_mecanico_input) if porc_mecanico_input != '' else mec.porcentagem_padrao
            os_obj.valor_comissao_mecanico = round(os_obj.valor_mao_obra * (os_obj.porcentagem_comissao_mecanico / 100.0), 2)
    else:
        os_obj.mecanico_id = None
        os_obj.porcentagem_comissao_mecanico = 0.0
        os_obj.valor_comissao_mecanico = 0.0

    db.session.commit()
    flash("Comissões e responsáveis da OS atualizados com sucesso!", "success")
    return redirect(url_for('ver_os', os_id=os_obj.id))


@app.route('/os/<int:os_id>/reabrir', methods=['POST'])
@login_required
def reabrir_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()

    if os.status == 'CONCLUIDA':
        # 1. Estorna a transação financeira do livro caixa da empresa
        transacao = Transacao.query.filter_by(os_id=os.id, empresa_id=g.empresa.id).first()
        if transacao:
            db.session.delete(transacao)

        # 2. Devolve estoque dos itens vinculados a produtos
        for it in os.itens:
            if it.produto_id:
                prod = Produto.query.filter_by(id=it.produto_id, empresa_id=g.empresa.id).first()
                if prod:
                    prod.estoque_atual = round(prod.estoque_atual + (it.quantidade or 1.0), 2)

        # 3. Exclui os itens lançados para poder cadastrar a lista correta
        ItemOS.query.filter_by(os_id=os.id).delete()

        # 4. Retorna a OS para o estado ABERTA
        os.status = 'ABERTA'
        os.valor_pecas = 0.0
        os.valor_mao_obra = 0.0
        os.valor_total = 0.0
        os.forma_pagamento = None
        os.data_conclusao = None
        os.retorno_previsto = None

        db.session.commit()
        flash(f"A OS #{os.id:04d} foi reaberta para edição, itens de estoque devolvidos e o caixa estornado.", "success")

    return redirect(url_for('ver_os', os_id=os.id))


@app.route('/os/<int:os_id>/alterar-retorno', methods=['POST'])
@login_required
def alterar_retorno_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()

    data_manual = request.form.get('data_retorno', '').strip()
    opcao = request.form.get('opcao_retorno', '').strip()

    if data_manual:
        try:
            os.retorno_previsto = date.fromisoformat(data_manual)
            db.session.commit()
            flash(f"Data de retorno da OS #{os.id:04d} alterada para {os.retorno_previsto.strftime('%d/%m/%Y')}!", "success")
        except (ValueError, TypeError):
            flash("Data de retorno inválida.", "error")
    elif opcao == 'sem_retorno':
        os.retorno_previsto = None
        db.session.commit()
        flash(f"Lembrete de retorno da OS #{os.id:04d} removido.", "info")
    elif opcao:
        try:
            dias = int(opcao)
            base = os.data_conclusao or date.today()
            os.retorno_previsto = base + timedelta(days=dias)
            db.session.commit()
            flash(f"Data de retorno da OS #{os.id:04d} recalculada para {os.retorno_previsto.strftime('%d/%m/%Y')} (+{dias} dias)!", "success")
        except (ValueError, TypeError):
            flash("Opção de dias inválida.", "error")

    next_url = request.form.get('next') or request.referrer or url_for('ver_os', os_id=os.id)
    return redirect(next_url)


# --- ETAPA DE ANDAMENTO E RASTREAMENTO PÚBLICO EM TEMPO REAL ---

@app.route('/os/<int:os_id>/etapa', methods=['POST'])
@login_required
def alterar_etapa_os(os_id):
    os_obj = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()
    nova_etapa = request.form.get('etapa', '').strip().upper()
    etapas_validas = ['RECEBIDO', 'DIAGNOSTICO', 'EM_EXECUCAO', 'PRONTO', 'ENTREGUE']
    
    if nova_etapa in etapas_validas:
        os_obj.etapa_andamento = nova_etapa
        db.session.commit()
        flash(f"Etapa da OS #{os_obj.id:04d} atualizada para: {nova_etapa}", "success")
    else:
        flash("Etapa inválida.", "error")

    next_url = request.form.get('next') or request.referrer or url_for('ver_os', os_id=os_obj.id)
    return redirect(next_url)


@app.route('/status/<string:codigo_rastreio>')
def rastreio_publico_os(codigo_rastreio):
    """Página pública mobile-friendly para o cliente acompanhar o andamento em tempo real"""
    if not codigo_rastreio or len(codigo_rastreio) < 6:
        return render_template('404.html'), 404

    os_obj = OrdemServico.query.filter_by(codigo_rastreio=codigo_rastreio).first_or_404()
    empresa = os_obj.empresa
    return render_template('os_status_publico.html', os=os_obj, empresa=empresa)


# --- ASSINATURA DIGITAL DO CLIENTE NA TELA ---

@app.route('/os/<int:os_id>/salvar-assinatura', methods=['POST'])
@login_required
def salvar_assinatura_os(os_id):
    os_obj = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()
    
    if request.is_json:
        data = request.get_json() or {}
        assinatura_base64 = data.get('assinatura_base64', '').strip()
    else:
        assinatura_base64 = request.form.get('assinatura_base64', '').strip()
    
    if assinatura_base64 and assinatura_base64.startswith('data:image'):
        os_obj.assinatura_cliente_data = assinatura_base64
        os_obj.assinatura_data_hora = datetime.now()
        db.session.commit()
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'sucesso': True, 'mensagem': 'Assinatura registrada com sucesso!'})
        flash("Assinatura digital do cliente coletada e salva com sucesso!", "success")
    else:
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'sucesso': False, 'mensagem': 'Nenhum desenho de assinatura foi enviado.'}), 400
        flash("Nenhum desenho de assinatura foi enviado.", "warning")

    return redirect(url_for('ver_os', os_id=os_obj.id))


# --- IMPRESSÃO DE CUPOM TÉRMICO (BOBINA 80MM / 58MM) ---

@app.route('/os/<int:os_id>/cupom')
@login_required
def cupom_termico_os(os_id):
    os_obj = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()
    empresa = g.empresa
    return render_template('os_cupom.html', os=os_obj, empresa=empresa)


# --- CATÁLOGO DE PRODUTOS, PEÇAS & ESTOQUE ---

@app.route('/produtos')
@login_required
def lista_produtos():
    busca = request.args.get('busca', '').strip()
    tipo_filtro = request.args.get('tipo', '').strip().upper()
    alerta_estoque = request.args.get('alerta', '').strip()

    query = Produto.query.filter_by(empresa_id=g.empresa.id, ativo=True)
    if busca:
        query = query.filter(
            (Produto.nome.ilike(f'%{busca}%')) |
            (Produto.codigo.ilike(f'%{busca}%'))
        )
    if tipo_filtro in ['PECA', 'SERVICO']:
        query = query.filter_by(tipo=tipo_filtro)
    
    produtos = query.order_by(Produto.nome.asc()).all()

    if alerta_estoque == 'baixo':
        produtos = [p for p in produtos if p.tipo == 'PECA' and p.estoque_atual <= p.estoque_minimo]

    todos_itens = Produto.query.filter_by(empresa_id=g.empresa.id, ativo=True).all()
    total_pecas = sum(1 for p in todos_itens if p.tipo == 'PECA')
    total_servicos = sum(1 for p in todos_itens if p.tipo == 'SERVICO')
    baixo_estoque_count = sum(1 for p in todos_itens if p.tipo == 'PECA' and p.estoque_atual <= p.estoque_minimo)

    return render_template(
        'produtos.html',
        produtos=produtos,
        busca=busca,
        tipo_filtro=tipo_filtro,
        alerta_estoque=alerta_estoque,
        total_pecas=total_pecas,
        total_servicos=total_servicos,
        baixo_estoque_count=baixo_estoque_count
    )


@app.route('/produtos/novo', methods=['POST'])
@login_required
def novoProduto():
    nome = request.form.get('nome', '').strip()
    if not nome:
        flash("O nome do item é obrigatório.", "error")
        return redirect(url_for('lista_produtos'))

    tipo = request.form.get('tipo', 'PECA').strip().upper()
    codigo = request.form.get('codigo', '').strip()
    unidade = request.form.get('unidade', 'UN').strip().upper()

    def converter_valor(v):
        try:
            return max(0.0, float(str(v or '0').replace('.', '').replace(',', '.').strip()))
        except (ValueError, TypeError):
            return 0.0

    preco_custo = converter_valor(request.form.get('preco_custo', '0'))
    preco_venda = converter_valor(request.form.get('preco_venda', '0'))
    estoque_atual = converter_valor(request.form.get('estoque_atual', '0')) if tipo == 'PECA' else 0.0
    estoque_minimo = converter_valor(request.form.get('estoque_minimo', '0')) if tipo == 'PECA' else 0.0

    ncm = re.sub(r'\D', '', request.form.get('ncm', '').strip())
    cfop = re.sub(r'\D', '', request.form.get('cfop', '').strip())
    cod_serv = request.form.get('codigo_servico_municipal', '').strip()
    aliq_iss = converter_valor(request.form.get('aliquota_iss', '0'))

    item = Produto(
        empresa_id=g.empresa.id,
        codigo=codigo,
        nome=nome,
        tipo=tipo,
        preco_custo=preco_custo,
        preco_venda=preco_venda,
        estoque_atual=estoque_atual,
        estoque_minimo=estoque_minimo,
        unidade=unidade,
        ncm=ncm,
        cfop=cfop,
        codigo_servico_municipal=cod_serv,
        aliquota_iss=aliq_iss,
        ativo=True
    )
    db.session.add(item)
    db.session.commit()
    flash(f"'{nome}' cadastrado com sucesso!", "success")
    return redirect(url_for('lista_produtos'))


@app.route('/produtos/<int:produto_id>/editar', methods=['POST'])
@login_required
def editar_produto(produto_id):
    prod = Produto.query.filter_by(id=produto_id, empresa_id=g.empresa.id).first_or_404()
    nome = request.form.get('nome', '').strip()
    if not nome:
        flash("O nome é obrigatório.", "error")
        return redirect(url_for('lista_produtos'))

    def converter_valor(v):
        try:
            return max(0.0, float(str(v or '0').replace('.', '').replace(',', '.').strip()))
        except (ValueError, TypeError):
            return 0.0

    prod.nome = nome
    prod.codigo = request.form.get('codigo', '').strip()
    prod.tipo = request.form.get('tipo', prod.tipo).strip().upper()
    prod.unidade = request.form.get('unidade', prod.unidade).strip().upper()
    prod.preco_custo = converter_valor(request.form.get('preco_custo', prod.preco_custo))
    prod.preco_venda = converter_valor(request.form.get('preco_venda', prod.preco_venda))
    if prod.tipo == 'PECA':
        prod.estoque_atual = converter_valor(request.form.get('estoque_atual', prod.estoque_atual))
        prod.estoque_minimo = converter_valor(request.form.get('estoque_minimo', prod.estoque_minimo))
    prod.ncm = re.sub(r'\D', '', request.form.get('ncm', '').strip())
    prod.cfop = re.sub(r'\D', '', request.form.get('cfop', '').strip())
    prod.codigo_servico_municipal = request.form.get('codigo_servico_municipal', '').strip()
    prod.aliquota_iss = converter_valor(request.form.get('aliquota_iss', '0'))

    db.session.commit()
    flash(f"Item '{prod.nome}' atualizado com sucesso!", "success")
    return redirect(url_for('lista_produtos'))


@app.route('/produtos/<int:produto_id>/excluir', methods=['POST'])
@login_required
def excluir_produto(produto_id):
    prod = Produto.query.filter_by(id=produto_id, empresa_id=g.empresa.id).first_or_404()
    # Soft-delete para não quebrar integridade em ordens já existentes
    prod.ativo = False
    db.session.commit()
    flash(f"Item '{prod.nome}' removido do catálogo.", "info")
    return redirect(url_for('lista_produtos'))


@app.route('/api/produtos/busca')
@login_required
def api_busca_produtos():
    """Autocomplete rápido para lançamento de peças e serviços na OS"""
    termo = request.args.get('q', '').strip()
    tipo = request.args.get('tipo', '').strip().upper()

    query = Produto.query.filter_by(empresa_id=g.empresa.id, ativo=True)
    if termo:
        query = query.filter((Produto.nome.ilike(f'%{termo}%')) | (Produto.codigo.ilike(f'%{termo}%')))
    if tipo in ['PECA', 'SERVICO']:
        query = query.filter_by(tipo=tipo)

    resultados = query.limit(20).all()
    dados = []
    for p in resultados:
        dados.append({
            'id': p.id,
            'nome': p.nome,
            'codigo': p.codigo or '',
            'tipo': p.tipo,
            'preco_venda': float(p.preco_venda or 0.0),
            'estoque_atual': float(p.estoque_atual or 0.0),
            'unidade': p.unidade or 'UN',
            'ncm': p.ncm or '',
            'cfop': p.cfop or '',
            'codigo_servico_municipal': p.codigo_servico_municipal or '',
            'aliquota_iss': float(p.aliquota_iss or 0.0)
        })
    return jsonify(dados)


# --- ENTRADA DE MERCADORIAS POR NOTA (XML / PDF) ---

@app.route('/produtos/importar-nota', methods=['POST'])
@login_required
def importar_nota_produtos():
    arquivo = request.files.get('arquivo_nota')
    if not arquivo or not arquivo.filename:
        flash("Por favor, selecione um arquivo XML da NF-e ou PDF da DANFE.", "warning")
        return redirect(url_for('lista_produtos'))

    import importador_nota
    resultado = importador_nota.processar_arquivo_nota(arquivo)

    if not resultado.get('sucesso'):
        flash(resultado.get('erro', 'Falha ao processar arquivo de nota fiscal.'), "error")
        return redirect(url_for('lista_produtos'))

    # Cruzar itens da nota com o catálogo da empresa
    itens_decorados = []
    for item in resultado.get('itens', []):
        prod_existente = None
        if item.get('codigo'):
            prod_existente = Produto.query.filter_by(
                empresa_id=g.empresa.id,
                codigo=item['codigo'],
                ativo=True
            ).first()

        if not prod_existente and item.get('nome'):
            prod_existente = Produto.query.filter(
                Produto.empresa_id == g.empresa.id,
                Produto.ativo == True,
                Produto.nome.ilike(item['nome'].strip())
            ).first()

        qtd = float(item.get('quantidade') or 1.0)
        custo = float(item.get('preco_custo') or 0.0)

        if prod_existente:
            status_match = 'EXISTENTE'
            prod_id = prod_existente.id
            estoque_atual = float(prod_existente.estoque_atual or 0.0)
            novo_estoque = round(estoque_atual + qtd, 2)
            preco_venda_sugerido = float(prod_existente.preco_venda or 0.0)
            if preco_venda_sugerido == 0.0:
                preco_venda_sugerido = round(custo * 1.5, 2)
        else:
            status_match = 'NOVO'
            prod_id = None
            estoque_atual = 0.0
            novo_estoque = qtd
            preco_venda_sugerido = round(custo * 1.5, 2)

        itens_decorados.append({
            'status_match': status_match,
            'produto_id': prod_id,
            'codigo': item.get('codigo', ''),
            'nome': item.get('nome', ''),
            'ncm': item.get('ncm', ''),
            'cfop': item.get('cfop', ''),
            'unidade': item.get('unidade', 'UN'),
            'quantidade': qtd,
            'preco_custo': custo,
            'subtotal': round(qtd * custo, 2),
            'estoque_atual': estoque_atual,
            'novo_estoque': novo_estoque,
            'preco_venda_sugerido': preco_venda_sugerido
        })

    dados_nota = {
        'tipo_arquivo': resultado.get('tipo_arquivo'),
        'chave_acesso': resultado.get('chave_acesso'),
        'numero_nota': resultado.get('numero_nota'),
        'fornecedor_nome': resultado.get('fornecedor_nome'),
        'fornecedor_cnpj': resultado.get('fornecedor_cnpj'),
        'data_emissao': resultado.get('data_emissao')
    }

    return render_template(
        'produtos_conferencia_nota.html',
        dados_nota=dados_nota,
        itens=itens_decorados
    )


@app.route('/produtos/confirmar-entrada', methods=['POST'])
@login_required
def confirmar_entrada_produtos():
    indices = request.form.getlist('incluir_idx[]')
    if not indices:
        flash("Nenhum item foi selecionado para dar entrada no estoque.", "warning")
        return redirect(url_for('lista_produtos'))

    numero_nota = request.form.get('numero_nota', '').strip()
    fornecedor_nome = request.form.get('fornecedor_nome', '').strip()
    registrar_caixa = request.form.get('registrar_caixa') == '1'

    def converter_valor(v):
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return max(0.0, float(v))
        s = str(v).strip()
        if not s:
            return 0.0
        if ',' in s and '.' in s:
            if s.rfind(',') > s.rfind('.'):
                s = s.replace('.', '').replace(',', '.')
            else:
                s = s.replace(',', '')
        elif ',' in s:
            s = s.replace(',', '.')
        try:
            return max(0.0, float(s))
        except (ValueError, TypeError):
            return 0.0

    qtd_itens_processados = 0
    total_financeiro_compra = 0.0

    for idx in indices:
        nome = request.form.get(f'nome_{idx}', '').strip()
        if not nome:
            continue

        codigo = request.form.get(f'codigo_{idx}', '').strip()
        ncm = re.sub(r'\D', '', request.form.get(f'ncm_{idx}', '').strip())
        cfop = re.sub(r'\D', '', request.form.get(f'cfop_{idx}', '').strip())
        unidade = (request.form.get(f'unidade_{idx}', 'UN').strip().upper())[:10]
        qtd = converter_valor(request.form.get(f'qtd_{idx}', '1'))
        custo = converter_valor(request.form.get(f'custo_{idx}', '0'))
        venda = converter_valor(request.form.get(f'venda_{idx}', '0'))
        prod_id_str = request.form.get(f'prod_id_{idx}', '').strip()

        prod = None
        if prod_id_str:
            try:
                p_id = int(prod_id_str)
                prod = Produto.query.filter_by(id=p_id, empresa_id=g.empresa.id).first()
            except (ValueError, TypeError):
                prod = None

        if prod:
            # Incrementa produto existente
            prod.estoque_atual = round(prod.estoque_atual + qtd, 2)
            if custo > 0:
                prod.preco_custo = custo
            if venda > 0:
                prod.preco_venda = venda
            if ncm and not prod.ncm:
                prod.ncm = ncm
            if cfop and not prod.cfop:
                prod.cfop = cfop
            if not prod.ativo:
                prod.ativo = True
        else:
            # Cadastra novo produto no catálogo
            novo_prod = Produto(
                empresa_id=g.empresa.id,
                codigo=codigo,
                nome=nome,
                tipo='PECA',
                preco_custo=custo,
                preco_venda=venda,
                estoque_atual=qtd,
                estoque_minimo=1.0,
                unidade=unidade,
                ncm=ncm,
                cfop=cfop,
                ativo=True
            )
            db.session.add(novo_prod)

        qtd_itens_processados += 1
        total_financeiro_compra += (qtd * custo)

    # Registro opcional de saída financeira no Livro Caixa
    if registrar_caixa and total_financeiro_compra > 0:
        desc_caixa = f"Compra Mercadoria / Nota #{numero_nota}"
        if fornecedor_nome:
            desc_caixa += f" - {fornecedor_nome}"
        transacao = Transacao(
            empresa_id=g.empresa.id,
            tipo='DESPESA',
            descricao=desc_caixa,
            valor=round(total_financeiro_compra, 2),
            forma_pagamento='Boleto / Faturado',
            data_movimento=date.today()
        )
        db.session.add(transacao)

    db.session.commit()
    flash(f"Entrada concluída com sucesso! {qtd_itens_processados} produto(s) atualizado(s) no estoque.", "success")
    return redirect(url_for('lista_produtos'))


# --- FICHA DE CLIENTES E HISTÓRICO ISOLADO ---

@app.route('/clientes')
@login_required
def lista_clientes():
    busca = request.args.get('busca', '').strip()

    query = Cliente.query.filter_by(empresa_id=g.empresa.id)
    if busca:
        busca_limpa = re.sub(r'\D', '', busca)
        filtros = [
            Cliente.nome.ilike(f'%{busca}%'),
            Cliente.telefone.ilike(f'%{busca}%'),
            Cliente.cpf_cnpj.ilike(f'%{busca}%'),
            Cliente.email.ilike(f'%{busca}%')
        ]
        if busca_limpa:
            cpf_sem_pontos = db.func.replace(db.func.replace(db.func.replace(Cliente.cpf_cnpj, '.', ''), '-', ''), '/', '')
            tel_sem_pontos = db.func.replace(db.func.replace(db.func.replace(db.func.replace(Cliente.telefone, '(', ''), ')', ''), '-', ''), ' ', '')
            filtros.append(cpf_sem_pontos.ilike(f'%{busca_limpa}%'))
            filtros.append(tel_sem_pontos.ilike(f'%{busca_limpa}%'))
            # Se digitou 11 dígitos (CPF)
            if len(busca_limpa) == 11:
                cpf_fmt = f"{busca_limpa[:3]}.{busca_limpa[3:6]}.{busca_limpa[6:9]}-{busca_limpa[9:]}"
                filtros.append(Cliente.cpf_cnpj.ilike(f'%{cpf_fmt}%'))
            # Se digitou 14 dígitos (CNPJ)
            elif len(busca_limpa) == 14:
                cnpj_fmt = f"{busca_limpa[:2]}.{busca_limpa[2:5]}.{busca_limpa[5:8]}/{busca_limpa[8:12]}-{busca_limpa[12:]}"
                filtros.append(Cliente.cpf_cnpj.ilike(f'%{cnpj_fmt}%'))

        query = query.filter(or_(*filtros))

    clientes = query.order_by(Cliente.nome.asc()).all()
    return render_template('clientes.html', clientes=clientes, busca=busca)


@app.route('/api/clientes/busca')
@login_required
def api_busca_clientes():
    """Autocomplete rápido de clientes por nome, telefone ou CPF/CNPJ para abertura de OS e pedidos"""
    termo = request.args.get('q', '').strip()
    if not termo or len(termo) < 2:
        return jsonify([])

    termo_limpo = re.sub(r'\D', '', termo)
    filtros = [
        Cliente.nome.ilike(f'%{termo}%'),
        Cliente.telefone.ilike(f'%{termo}%'),
        Cliente.cpf_cnpj.ilike(f'%{termo}%'),
        Cliente.email.ilike(f'%{termo}%')
    ]
    if termo_limpo:
        cpf_sem_pontos = db.func.replace(db.func.replace(db.func.replace(Cliente.cpf_cnpj, '.', ''), '-', ''), '/', '')
        tel_sem_pontos = db.func.replace(db.func.replace(db.func.replace(db.func.replace(Cliente.telefone, '(', ''), ')', ''), '-', ''), ' ', '')
        filtros.append(cpf_sem_pontos.ilike(f'%{termo_limpo}%'))
        filtros.append(tel_sem_pontos.ilike(f'%{termo_limpo}%'))
        if len(termo_limpo) == 11:
            cpf_fmt = f"{termo_limpo[:3]}.{termo_limpo[3:6]}.{termo_limpo[6:9]}-{termo_limpo[9:]}"
            filtros.append(Cliente.cpf_cnpj.ilike(f'%{cpf_fmt}%'))
        elif len(termo_limpo) == 14:
            cnpj_fmt = f"{termo_limpo[:2]}.{termo_limpo[2:5]}.{termo_limpo[5:8]}/{termo_limpo[8:12]}-{termo_limpo[12:]}"
            filtros.append(Cliente.cpf_cnpj.ilike(f'%{cnpj_fmt}%'))

    resultados = Cliente.query.filter_by(empresa_id=g.empresa.id)\
        .filter(or_(*filtros))\
        .order_by(Cliente.nome.asc())\
        .limit(10).all()

    dados = []
    for c in resultados:
        veiculos_list = [{'id': v.id, 'placa': v.placa, 'modelo': v.modelo} for v in c.veiculos]
        dados.append({
            'id': c.id,
            'nome': c.nome,
            'telefone': c.telefone,
            'cpf_cnpj': c.cpf_cnpj or '',
            'email': c.email or '',
            'veiculos': veiculos_list
        })
    return jsonify(dados)


@app.route('/clientes/<int:cliente_id>')
@login_required
def detalhe_cliente(cliente_id):
    cliente = Cliente.query.filter_by(id=cliente_id, empresa_id=g.empresa.id).first_or_404()

    historico_os = OrdemServico.query.filter_by(
        cliente_id=cliente.id,
        empresa_id=g.empresa.id
    ).order_by(OrdemServico.id.desc()).all()
    total_gasto = sum(os.valor_total for os in historico_os if os.status == 'CONCLUIDA')

    return render_template(
        'cliente_detalhe.html',
        cliente=cliente,
        historico_os=historico_os,
        total_gasto=total_gasto
    )


@app.route('/clientes/novo', methods=['POST'])
@login_required
def novo_cliente():
    nome = request.form.get('nome', '').strip()
    telefone = request.form.get('telefone', '').strip()
    cpf_cnpj = request.form.get('cpf_cnpj', '').strip()
    email = request.form.get('email', '').strip().lower()
    endereco = request.form.get('endereco', '').strip()
    numero = request.form.get('numero', '').strip()
    bairro = request.form.get('bairro', '').strip()
    cidade = request.form.get('cidade', '').strip()
    uf = request.form.get('uf', '').strip().upper()
    cep = request.form.get('cep', '').strip()

    veiculo_modelo = request.form.get('veiculo_modelo', '').strip()
    veiculo_placa = request.form.get('veiculo_placa', '').strip().upper()

    if not nome or not telefone:
        flash("Nome e telefone do cliente são obrigatórios.", "error")
        return redirect(url_for('lista_clientes'))

    # Verifica se já existe cliente com esse telefone na empresa
    cliente = Cliente.query.filter_by(empresa_id=g.empresa.id, telefone=telefone).first()
    if cliente:
        flash(f"Já existe um cliente cadastrado com o telefone '{telefone}': {cliente.nome}.", "warning")
        return redirect(url_for('detalhe_cliente', cliente_id=cliente.id))

    cliente = Cliente(
        empresa_id=g.empresa.id,
        nome=nome,
        telefone=telefone,
        cpf_cnpj=cpf_cnpj,
        email=email,
        endereco=endereco,
        numero=numero,
        bairro=bairro,
        cidade=cidade,
        uf=uf,
        cep=cep
    )
    db.session.add(cliente)
    db.session.flush()

    if veiculo_placa:
        veiculo = Veiculo(cliente_id=cliente.id, placa=veiculo_placa, modelo=veiculo_modelo or "Veículo")
        db.session.add(veiculo)

    db.session.commit()
    flash(f"Cliente '{cliente.nome}' cadastrado com sucesso!", "success")
    return redirect(url_for('detalhe_cliente', cliente_id=cliente.id))


@app.route('/clientes/<int:cliente_id>/editar', methods=['POST'])
@login_required
def editar_cliente(cliente_id):
    cliente = Cliente.query.filter_by(id=cliente_id, empresa_id=g.empresa.id).first_or_404()

    nome = request.form.get('nome', '').strip()
    telefone = request.form.get('telefone', '').strip()

    if not nome or not telefone:
        flash("Nome e telefone são obrigatórios.", "error")
        return redirect(request.referrer or url_for('detalhe_cliente', cliente_id=cliente.id))

    cliente.nome = nome
    cliente.telefone = telefone
    if 'cpf_cnpj' in request.form:
        cliente.cpf_cnpj = request.form.get('cpf_cnpj', '').strip()
    if 'email' in request.form:
        cliente.email = request.form.get('email', '').strip().lower()
    if 'endereco' in request.form:
        cliente.endereco = request.form.get('endereco', '').strip()
    if 'numero' in request.form:
        cliente.numero = request.form.get('numero', '').strip()
    if 'bairro' in request.form:
        cliente.bairro = request.form.get('bairro', '').strip()
    if 'cidade' in request.form:
        cliente.cidade = request.form.get('cidade', '').strip()
    if 'uf' in request.form:
        cliente.uf = request.form.get('uf', '').strip().upper()
    if 'cep' in request.form:
        cliente.cep = request.form.get('cep', '').strip()

    # Atualiza veículos existentes
    veiculo_ids = request.form.getlist('veiculo_id[]')
    veiculo_modelos = request.form.getlist('veiculo_modelo[]')
    veiculo_placas = request.form.getlist('veiculo_placa[]')

    for v_id_str, mod, plc in zip(veiculo_ids, veiculo_modelos, veiculo_placas):
        try:
            v_id = int(v_id_str)
            v = Veiculo.query.filter_by(id=v_id, cliente_id=cliente.id).first()
            if v:
                v.modelo = mod.strip() or v.modelo
                v.placa = plc.strip().upper() or v.placa
        except (ValueError, TypeError):
            continue

    # Adiciona novo veículo caso preenchido
    novo_mod = request.form.get('novo_veiculo_modelo', '').strip()
    novo_plc = request.form.get('novo_veiculo_placa', '').strip().upper()
    if novo_plc:
        novo_v = Veiculo(cliente_id=cliente.id, placa=novo_plc, modelo=novo_mod or "Veículo")
        db.session.add(novo_v)

    db.session.commit()
    flash(f"Cadastro de '{cliente.nome}' atualizado com sucesso!", "success")
    return redirect(request.referrer or url_for('detalhe_cliente', cliente_id=cliente.id))


# --- EXTRATO FINANCEIRO ISOLADO ---

@app.route('/financeiro')
@login_required
def relatorio_financeiro():
    hoje = date.today()
    mes_selecionado = int(request.args.get('mes', hoje.month))
    ano_selecionado = int(request.args.get('ano', hoje.year))

    transacoes = Transacao.query.filter(
        Transacao.empresa_id == g.empresa.id,
        db.extract('month', Transacao.data_movimento) == mes_selecionado,
        db.extract('year', Transacao.data_movimento) == ano_selecionado
    ).order_by(Transacao.data_movimento.desc(), Transacao.id.desc()).all()

    total_receitas = sum(t.valor for t in transacoes if t.tipo == 'RECEITA')
    total_despesas = sum(t.valor for t in transacoes if t.tipo == 'DESPESA')
    resultado_liquido = total_receitas - total_despesas

    totais_pagamento = {}
    for t in transacoes:
        if t.tipo == 'RECEITA':
            fp = t.forma_pagamento or 'Não Informado'
            totais_pagamento[fp] = round(totais_pagamento.get(fp, 0.0) + t.valor, 2)

    return render_template(
        'financeiro.html',
        transacoes=transacoes,
        total_receitas=total_receitas,
        total_despesas=total_despesas,
        resultado_liquido=resultado_liquido,
        totais_pagamento=totais_pagamento,
        mes_atual=mes_selecionado,
        ano_atual=ano_selecionado
    )


@app.route('/financeiro/exportar')
@login_required
def exportar_financeiro():
    hoje = date.today()
    mes = int(request.args.get('mes', hoje.month))
    ano = int(request.args.get('ano', hoje.year))

    transacoes = Transacao.query.filter(
        Transacao.empresa_id == g.empresa.id,
        db.extract('month', Transacao.data_movimento) == mes,
        db.extract('year', Transacao.data_movimento) == ano
    ).order_by(Transacao.data_movimento.asc()).all()

    si = StringIO()
    writer = csv.writer(si, delimiter=';')
    writer.writerow(['Data', 'Tipo', 'Descricao', 'Forma Pagamento', 'Valor (R$)', 'OS Origem'])

    for t in transacoes:
        writer.writerow([
            t.data_movimento.strftime('%d/%m/%Y'),
            t.tipo,
            t.descricao,
            t.forma_pagamento or '-',
            f"{t.valor:.2f}".replace('.', ','),
            f"#{t.os_id:04d}" if t.os_id else 'Manual'
        ])

    output = Response(si.getvalue(), mimetype='text/csv')
    output.headers["Content-Disposition"] = f"attachment; filename=extrato_{mes:02d}_{ano}.csv"
    return output


# --- RETORNOS PREVENTIVOS ISOLADOS ---

@app.route('/retornos')
@login_required
def retornos_preventivos():
    hoje = date.today()
    janela_aviso = hoje + timedelta(days=15)

    alertas = OrdemServico.query.filter(
        OrdemServico.empresa_id == g.empresa.id,
        OrdemServico.retorno_previsto.between(hoje, janela_aviso),
        OrdemServico.status == 'CONCLUIDA'
    ).order_by(OrdemServico.retorno_previsto.asc()).all()

# --- GESTÃO DE COLABORADORES (VENDEDORES & MECÂNICOS/TÉCNICOS) ---

@app.route('/colaboradores')
@login_required
def lista_colaboradores():
    hoje = date.today()
    primeiro_dia_mes = hoje.replace(day=1)

    colaboradores = Colaborador.query.filter_by(empresa_id=g.empresa.id).order_by(Colaborador.nome.asc()).all()

    # Comissões acumuladas do mês atual para cada colaborador
    comissoes_mes = {}
    for c in colaboradores:
        total = 0.0
        if c.funcao == 'VENDEDOR':
            ordens = OrdemServico.query.filter(
                OrdemServico.empresa_id == g.empresa.id,
                OrdemServico.vendedor_id == c.id,
                OrdemServico.status == 'CONCLUIDA',
                OrdemServico.data_conclusao >= primeiro_dia_mes
            ).all()
            for o in ordens:
                val = o.valor_comissao_vendedor
                if val == 0 and o.porcentagem_comissao_vendedor:
                    base = o.valor_pecas if c.tipo_base == 'PECAS' else o.valor_total
                    val = round(base * (o.porcentagem_comissao_vendedor / 100.0), 2)
                total += val
        else:
            ordens = OrdemServico.query.filter(
                OrdemServico.empresa_id == g.empresa.id,
                OrdemServico.mecanico_id == c.id,
                OrdemServico.status == 'CONCLUIDA',
                OrdemServico.data_conclusao >= primeiro_dia_mes
            ).all()
            for o in ordens:
                val = o.valor_comissao_mecanico
                if val == 0 and o.porcentagem_comissao_mecanico:
                    val = round(o.valor_mao_obra * (o.porcentagem_comissao_mecanico / 100.0), 2)
                total += val
        comissoes_mes[c.id] = round(total, 2)

    return render_template('colaboradores.html', colaboradores=colaboradores, comissoes_mes=comissoes_mes)


@app.route('/colaboradores/novo', methods=['POST'])
@login_required
def novo_colaborador():
    nome = request.form.get('nome', '').strip()
    if not nome:
        flash("O nome do colaborador é obrigatório.", "error")
        return redirect(url_for('lista_colaboradores'))

    funcao = request.form.get('funcao', 'VENDEDOR').strip().upper()
    if funcao not in ['VENDEDOR', 'MECANICO', 'TECNICO']:
        funcao = 'VENDEDOR'

    telefone = request.form.get('telefone', '').strip()
    chave_pix = request.form.get('chave_pix', '').strip()
    porcentagem = converter_valor_monetario(request.form.get('porcentagem_padrao', '0'))
    tipo_base = request.form.get('tipo_base', 'TOTAL').strip().upper()
    if tipo_base not in ['TOTAL', 'PECAS', 'MAO_DE_OBRA']:
        tipo_base = 'TOTAL' if funcao == 'VENDEDOR' else 'MAO_DE_OBRA'

    colab = Colaborador(
        empresa_id=g.empresa.id,
        nome=nome,
        funcao=funcao,
        telefone=telefone,
        chave_pix=chave_pix,
        porcentagem_padrao=porcentagem,
        tipo_base=tipo_base,
        ativo=True,
        data_cadastro=date.today()
    )
    db.session.add(colab)
    db.session.commit()
    flash(f"Colaborador(a) '{nome}' cadastrado(a) com sucesso com {porcentagem}% de comissão padrão!", "success")
    return redirect(url_for('lista_colaboradores'))


@app.route('/colaboradores/<int:colaborador_id>/editar', methods=['POST'])
@login_required
def editar_colaborador(colaborador_id):
    colab = Colaborador.query.filter_by(id=colaborador_id, empresa_id=g.empresa.id).first_or_404()

    nome = request.form.get('nome', '').strip()
    if not nome:
        flash("O nome não pode ficar em branco.", "error")
        return redirect(url_for('lista_colaboradores'))

    funcao = request.form.get('funcao', colab.funcao).strip().upper()
    telefone = request.form.get('telefone', '').strip()
    chave_pix = request.form.get('chave_pix', '').strip()
    porcentagem = converter_valor_monetario(request.form.get('porcentagem_padrao', '0'))
    tipo_base = request.form.get('tipo_base', colab.tipo_base).strip().upper()
    ativo = request.form.get('ativo') == '1'

    colab.nome = nome
    colab.funcao = funcao
    colab.telefone = telefone
    colab.chave_pix = chave_pix
    colab.porcentagem_padrao = porcentagem
    colab.tipo_base = tipo_base
    colab.ativo = ativo

    db.session.commit()
    flash(f"Dados de '{colab.nome}' atualizados com sucesso!", "success")
    return redirect(url_for('lista_colaboradores'))


@app.route('/colaboradores/<int:colaborador_id>/toggle-status', methods=['POST'])
@login_required
def toggle_status_colaborador(colaborador_id):
    colab = Colaborador.query.filter_by(id=colaborador_id, empresa_id=g.empresa.id).first_or_404()
    colab.ativo = not colab.ativo
    db.session.commit()
    status_str = "ativado" if colab.ativo else "desativado"
    flash(f"Colaborador '{colab.nome}' {status_str} com sucesso.", "info")
    return redirect(url_for('lista_colaboradores'))


@app.route('/api/colaboradores')
@login_required
def api_colaboradores():
    colabs = Colaborador.query.filter_by(empresa_id=g.empresa.id, ativo=True).order_by(Colaborador.nome.asc()).all()
    return jsonify([c.to_dict() for c in colabs])


# --- CÁLCULO E RELATÓRIO DE COMISSÕES (POR DIA, POR SEMANA E POR MÊS) ---

@app.route('/comissoes')
@login_required
def relatorio_comissoes():
    hoje = date.today()
    periodo = request.args.get('periodo', 'mes').strip().lower() # 'hoje', 'semana', 'mes', 'personalizado'
    colaborador_id_filtro = request.args.get('colaborador_id', type=int)
    funcao_filtro = request.args.get('funcao', '').strip().upper()
    status_filtro = request.args.get('status', 'CONCLUIDA').strip().upper()

    # Define o intervalo de datas
    if periodo == 'hoje':
        data_inicio = hoje
        data_fim = hoje
    elif periodo == 'semana':
        # Últimos 7 dias
        data_inicio = hoje - timedelta(days=6)
        data_fim = hoje
    elif periodo == 'personalizado':
        data_inicio_str = request.args.get('data_inicio', '').strip()
        data_fim_str = request.args.get('data_fim', '').strip()
        try:
            data_inicio = date.fromisoformat(data_inicio_str) if data_inicio_str else hoje.replace(day=1)
            data_fim = date.fromisoformat(data_fim_str) if data_fim_str else hoje
        except (ValueError, TypeError):
            data_inicio = hoje.replace(day=1)
            data_fim = hoje
    else:
        # Padrão: Mês atual
        periodo = 'mes'
        data_inicio = hoje.replace(day=1)
        data_fim = hoje

    # Todos os colaboradores da empresa
    colaboradores = Colaborador.query.filter_by(empresa_id=g.empresa.id).order_by(Colaborador.nome.asc()).all()

    # Busca ordens da empresa no período
    query_os = OrdemServico.query.filter(
        OrdemServico.empresa_id == g.empresa.id,
        or_(
            OrdemServico.data_conclusao.between(data_inicio, data_fim),
            OrdemServico.data_abertura.between(data_inicio, data_fim)
        )
    )

    if status_filtro and status_filtro != 'TODAS':
        query_os = query_os.filter(OrdemServico.status == status_filtro)

    ordens_periodo = query_os.order_by(OrdemServico.id.desc()).all()

    # Inicializa resumo consolidado para cada colaborador
    resumo_colaboradores = {}
    for c in colaboradores:
        resumo_colaboradores[c.id] = {
            'colaborador': c,
            'qtd_os': 0,
            'base_total': 0.0,
            'comissao_total': 0.0,
            'ordens': []
        }

    total_comissoes_geral = 0.0
    total_comissoes_vendedores = 0.0
    total_comissoes_mecanicos = 0.0
    extrato_itens = []

    for os_item in ordens_periodo:
        # 1. Checa comissão do Vendedor
        if os_item.vendedor_id and os_item.vendedor_id in resumo_colaboradores:
            if not funcao_filtro or funcao_filtro == 'VENDEDOR':
                c = resumo_colaboradores[os_item.vendedor_id]['colaborador']
                base_calc = os_item.valor_pecas if c.tipo_base == 'PECAS' else os_item.valor_total
                porc = os_item.porcentagem_comissao_vendedor if os_item.porcentagem_comissao_vendedor is not None else c.porcentagem_padrao
                val_comissao = os_item.valor_comissao_vendedor
                if (val_comissao == 0.0 or val_comissao is None) and porc > 0 and base_calc > 0:
                    val_comissao = round(base_calc * (porc / 100.0), 2)

                resumo_colaboradores[os_item.vendedor_id]['qtd_os'] += 1
                resumo_colaboradores[os_item.vendedor_id]['base_total'] += base_calc
                resumo_colaboradores[os_item.vendedor_id]['comissao_total'] += val_comissao
                total_comissoes_vendedores += val_comissao
                total_comissoes_geral += val_comissao

                item_extrato = {
                    'os': os_item,
                    'colaborador': c,
                    'papel': 'Vendedor / Comercial',
                    'base': base_calc,
                    'porcentagem': porc,
                    'valor_comissao': val_comissao,
                    'data': os_item.data_conclusao or os_item.data_abertura
                }
                resumo_colaboradores[os_item.vendedor_id]['ordens'].append(item_extrato)
                extrato_itens.append(item_extrato)

        # 2. Checa comissão do Mecânico / Técnico
        if os_item.mecanico_id and os_item.mecanico_id in resumo_colaboradores:
            if not funcao_filtro or funcao_filtro in ['MECANICO', 'TECNICO']:
                c = resumo_colaboradores[os_item.mecanico_id]['colaborador']
                base_calc = os_item.valor_mao_obra
                porc = os_item.porcentagem_comissao_mecanico if os_item.porcentagem_comissao_mecanico is not None else c.porcentagem_padrao
                val_comissao = os_item.valor_comissao_mecanico
                if (val_comissao == 0.0 or val_comissao is None) and porc > 0 and base_calc > 0:
                    val_comissao = round(base_calc * (porc / 100.0), 2)

                resumo_colaboradores[os_item.mecanico_id]['qtd_os'] += 1
                resumo_colaboradores[os_item.mecanico_id]['base_total'] += base_calc
                resumo_colaboradores[os_item.mecanico_id]['comissao_total'] += val_comissao
                total_comissoes_mecanicos += val_comissao
                total_comissoes_geral += val_comissao

                item_extrato = {
                    'os': os_item,
                    'colaborador': c,
                    'papel': 'Mecânico / Mão de Obra',
                    'base': base_calc,
                    'porcentagem': porc,
                    'valor_comissao': val_comissao,
                    'data': os_item.data_conclusao or os_item.data_abertura
                }
                resumo_colaboradores[os_item.mecanico_id]['ordens'].append(item_extrato)
                extrato_itens.append(item_extrato)

    # Filtrar por colaborador se selecionado
    if colaborador_id_filtro:
        resumo_colaboradores = {k: v for k, v in resumo_colaboradores.items() if k == colaborador_id_filtro}
        extrato_itens = [e for e in extrato_itens if e['colaborador'].id == colaborador_id_filtro]
        total_comissoes_geral = sum(v['comissao_total'] for v in resumo_colaboradores.values())

    # Arredondar totais
    for v in resumo_colaboradores.values():
        v['base_total'] = round(v['base_total'], 2)
        v['comissao_total'] = round(v['comissao_total'], 2)

    total_comissoes_geral = round(total_comissoes_geral, 2)
    total_comissoes_vendedores = round(total_comissoes_vendedores, 2)
    total_comissoes_mecanicos = round(total_comissoes_mecanicos, 2)

    return render_template(
        'comissoes.html',
        colaboradores=colaboradores,
        resumo_colaboradores=resumo_colaboradores,
        extrato_itens=extrato_itens,
        periodo=periodo,
        data_inicio=data_inicio,
        data_fim=data_fim,
        colaborador_id_filtro=colaborador_id_filtro,
        funcao_filtro=funcao_filtro,
        status_filtro=status_filtro,
        total_geral=total_comissoes_geral,
        total_vendedores=total_comissoes_vendedores,
        total_mecanicos=total_comissoes_mecanicos,
        total_atendimentos=len(extrato_itens)
    )


# --- BACKUP DIRETO ---

@app.route('/sistema/backup')
@login_required
def backup_banco():
    caminho_db = DB_PATH
    if not os.path.exists(caminho_db):
        caminho_db = os.path.join(BASE_DIR, 'instance', 'painel_gestao.db')
    if not os.path.exists(caminho_db):
        caminho_db = os.path.join(BASE_DIR, 'painel_gestao.db')

    data_str = date.today().strftime('%Y%m%d')
    return send_file(
        caminho_db,
        as_attachment=True,
        download_name=f"backup_painel_{data_str}.db"
    )


# --- CONFIGURAÇÕES DA EMPRESA (WHITE-LABEL) ---

@app.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes_empresa():
    empresa = g.empresa

    if request.method == 'POST':
        empresa.nome_empresa = request.form.get('nome_empresa', '').strip() or 'Minha Empresa'
        empresa.subtitulo = request.form.get('subtitulo', '').strip()
        empresa.cnpj_cpf = request.form.get('cnpj_cpf', '').strip()
        empresa.telefone = request.form.get('telefone', '').strip()
        empresa.whatsapp = request.form.get('whatsapp', '').strip()
        empresa.endereco = request.form.get('endereco', '').strip()
        empresa.cidade_uf = request.form.get('cidade_uf', '').strip()
        empresa.mensagem_rodape = request.form.get('mensagem_rodape', '').strip()

        tipo_negocio = request.form.get('tipo_negocio', '').strip().upper()
        if tipo_negocio in ['OFICINA', 'LOJA']:
            empresa.tipo_negocio = tipo_negocio

        # Atualização de e-mail e senha
        novo_email = request.form.get('email', '').strip().lower()
        if novo_email and novo_email != empresa.email:
            ja_existe = Empresa.query.filter_by(email=novo_email).first()
            if ja_existe:
                flash('Este e-mail já está sendo utilizado por outra empresa.', 'error')
            else:
                empresa.email = novo_email

        nova_senha = request.form.get('nova_senha', '').strip()
        if nova_senha:
            if len(nova_senha) < 6:
                flash('A nova senha deve ter no mínimo 6 dígitos.', 'error')
            else:
                empresa.senha_hash = generate_password_hash(nova_senha)
                flash('Senha atualizada com sucesso!', 'info')

        if 'logo' in request.files:
            file = request.files['logo']
            if file and file.filename != '':
                ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
                if ext in ['png', 'jpg', 'jpeg', 'webp', 'svg']:
                    file_bytes = file.read()
                    mime = 'image/svg+xml' if ext == 'svg' else f'image/{ext if ext != "jpg" else "jpeg"}'
                    b64_str = base64.b64encode(file_bytes).decode('utf-8')
                    empresa.logo_base64 = f"data:{mime};base64,{b64_str}"

                    nome_arquivo = f"logo_{empresa.id}_{int(time.time())}.{ext}"
                    caminho_salvar = os.path.join(app.config['UPLOAD_FOLDER'], nome_arquivo)
                    with open(caminho_salvar, 'wb') as f_save:
                        f_save.write(file_bytes)
                    empresa.logo_filename = f"uploads/{nome_arquivo}"
                else:
                    flash('Formato de logo inválido! Envie imagem PNG, JPG, WEBP ou SVG.', 'error')

        db.session.commit()
        flash('Dados da empresa atualizados com sucesso!', 'success')
        return redirect(url_for('configuracoes_empresa'))

    return render_template('configuracoes.html', empresa=empresa)


@app.route('/configuracoes/remover-logo', methods=['POST'])
@login_required
def remover_logo_empresa():
    empresa = g.empresa
    if empresa.logo_filename:
        caminho_logo = os.path.join(app.root_path, 'static', empresa.logo_filename)
        if os.path.exists(caminho_logo):
            try:
                os.remove(caminho_logo)
            except Exception:
                pass
    empresa.logo_filename = None
    empresa.logo_base64 = None
    db.session.commit()
    flash('Logotipo removido com sucesso. O sistema exibirá o nome da empresa.', 'info')
    return redirect(url_for('configuracoes_empresa'))


# --- MÓDULO FISCAL & EMISSÃO DE NOTAS (FOCUS NFE) ---

@app.route('/configuracoes/fiscal', methods=['GET', 'POST'])
@login_required
def configuracoes_fiscal():
    empresa = g.empresa

    # Trava de Segurança SaaS: apenas empresas com o plano fiscal liberado pelo Dono Master podem acessar
    if not empresa.permite_emissao_fiscal and not empresa.is_admin:
        return render_template('fiscal_bloqueado.html', empresa=empresa)

    if request.method == 'POST':
        empresa.fiscal_ativo = (request.form.get('fiscal_ativo') == 'on')
        empresa.fiscal_ambiente = request.form.get('fiscal_ambiente', 'HOMOLOGACAO').strip().upper()
        empresa.fiscal_cnpj = re.sub(r'\D', '', request.form.get('fiscal_cnpj', ''))
        empresa.fiscal_razao_social = request.form.get('fiscal_razao_social', '').strip()
        empresa.fiscal_nome_fantasia = request.form.get('fiscal_nome_fantasia', '').strip()
        empresa.fiscal_inscricao_municipal = request.form.get('fiscal_inscricao_municipal', '').strip()
        empresa.fiscal_inscricao_estadual = request.form.get('fiscal_inscricao_estadual', '').strip()
        empresa.fiscal_codigo_municipio = re.sub(r'\D', '', request.form.get('fiscal_codigo_municipio', '5208707')) or '5208707'
        
        try:
            empresa.fiscal_regime_tributario = int(request.form.get('fiscal_regime_tributario', 1))
        except (ValueError, TypeError):
            empresa.fiscal_regime_tributario = 1

        empresa.fiscal_token_focus = request.form.get('fiscal_token_focus', '').strip()

        nova_senha_cert = request.form.get('fiscal_certificado_senha', '').strip()
        if nova_senha_cert:
            empresa.fiscal_certificado_senha = nova_senha_cert

        # Upload do Certificado Digital A1 (.pfx ou .p12)
        if 'certificado' in request.files:
            file = request.files['certificado']
            if file and file.filename != '':
                ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
                if ext in ['pfx', 'p12']:
                    cert_folder = os.path.join(INSTANCE_DIR, 'certificados')
                    os.makedirs(cert_folder, exist_ok=True)
                    cert_filename = f"cert_{empresa.id}_{int(time.time())}.{ext}"
                    caminho_cert = os.path.join(cert_folder, cert_filename)
                    file.save(caminho_cert)
                    empresa.fiscal_certificado_filename = cert_filename
                    flash("Certificado Digital A1 enviado com sucesso!", "info")
                else:
                    flash("Formato de certificado inválido! Envie um arquivo .pfx ou .p12.", "error")

        db.session.commit()
        flash("Configurações do Módulo Fiscal atualizadas com sucesso!", "success")
        return redirect(url_for('configuracoes_fiscal'))

    return render_template('configuracoes_fiscal.html', empresa=empresa)


@app.route('/configuracoes/fiscal/remover-certificado', methods=['POST'])
@login_required
def remover_certificado_fiscal():
    empresa = g.empresa
    if not empresa.permite_emissao_fiscal and not empresa.is_admin:
        return redirect(url_for('dashboard'))

    if empresa.fiscal_certificado_filename:
        caminho = os.path.join(INSTANCE_DIR, 'certificados', empresa.fiscal_certificado_filename)
        if os.path.exists(caminho):
            try:
                os.remove(caminho)
            except Exception:
                pass
        empresa.fiscal_certificado_filename = None
        empresa.fiscal_certificado_senha = ""
        db.session.commit()
        flash("Certificado Digital removido com sucesso.", "info")

    return redirect(url_for('configuracoes_fiscal'))


@app.route('/os/<int:os_id>/emitir-nfse', methods=['POST'])
@login_required
def emitir_nfse_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()
    empresa = g.empresa

    # Trava de Segurança SaaS
    if not empresa.permite_emissao_fiscal and not empresa.is_admin:
        flash("Sua empresa não possui o Módulo Fiscal contratado. Fale com o suporte para ativar.", "warning")
        return redirect(url_for('ver_os', os_id=os.id))

    if not empresa.fiscal_ativo or not empresa.fiscal_token_focus:
        flash("O Módulo Fiscal não está ativo ou o Token da Focus NFe não foi configurado nas Configurações Fiscais.", "error")
        return redirect(url_for('configuracoes_fiscal'))

    itens_servico = [it for it in os.itens if it.tipo == 'SERVICO']
    if not itens_servico:
        flash("Esta Ordem de Serviço não possui itens de serviço/mão de obra para emissão de NFS-e.", "warning")
        return redirect(url_for('ver_os', os_id=os.id))

    import focus_nfe
    sucesso, res = focus_nfe.autorizar_nfse(empresa, os, itens_servico)

    if sucesso:
        nova_nota = NotaFiscal(
            empresa_id=empresa.id,
            ordem_servico_id=os.id,
            tipo_nota='NFSE',
            referencia_uuid=res['referencia'],
            status=res.get('status', 'PROCESSANDO'),
            mensagem_sefaz=res.get('mensagem', ''),
            valor_total=sum(float(it.subtotal or 0.0) for it in itens_servico),
            data_emissao=date.today()
        )
        db.session.add(nova_nota)
        db.session.commit()
        flash("NFS-e enviada com sucesso para a Prefeitura! Status: " + res.get('status', 'PROCESSANDO'), "success")
    else:
        flash(f"Erro ao emitir NFS-e: {res}", "error")

    return redirect(url_for('ver_os', os_id=os.id))


@app.route('/os/<int:os_id>/emitir-nfce', methods=['POST'])
@login_required
def emitir_nfce_os(os_id):
    os = OrdemServico.query.filter_by(id=os_id, empresa_id=g.empresa.id).first_or_404()
    empresa = g.empresa

    if not empresa.permite_emissao_fiscal and not empresa.is_admin:
        flash("Sua empresa não possui o Módulo Fiscal contratado.", "warning")
        return redirect(url_for('ver_os', os_id=os.id))

    if not empresa.fiscal_ativo or not empresa.fiscal_token_focus:
        flash("O Módulo Fiscal não está ativo ou o Token da Focus NFe não foi configurado.", "error")
        return redirect(url_for('configuracoes_fiscal'))

    itens_pecas = [it for it in os.itens if it.tipo == 'PECA']
    if not itens_pecas:
        flash("Esta Ordem de Serviço não possui peças ou produtos para emissão de NFC-e.", "warning")
        return redirect(url_for('ver_os', os_id=os.id))

    import focus_nfe
    sucesso, res = focus_nfe.autorizar_nfce(empresa, os, itens_pecas)

    if sucesso:
        nova_nota = NotaFiscal(
            empresa_id=empresa.id,
            ordem_servico_id=os.id,
            tipo_nota='NFCE',
            referencia_uuid=res['referencia'],
            status=res.get('status', 'PROCESSANDO'),
            mensagem_sefaz=res.get('mensagem', ''),
            valor_total=sum(float(it.subtotal or 0.0) for it in itens_pecas),
            data_emissao=date.today()
        )
        db.session.add(nova_nota)
        db.session.commit()
        flash("NFC-e enviada com sucesso para a SEFAZ! Status: " + res.get('status', 'PROCESSANDO'), "success")
    else:
        flash(f"Erro ao emitir NFC-e: {res}", "error")

    return redirect(url_for('ver_os', os_id=os.id))


@app.route('/fiscal/nota/<int:nota_id>/consultar', methods=['POST'])
@login_required
def consultar_status_nota(nota_id):
    nota = NotaFiscal.query.filter_by(id=nota_id, empresa_id=g.empresa.id).first_or_404()
    import focus_nfe
    sucesso, res = focus_nfe.consultar_nfse(g.empresa, nota.referencia_uuid)

    if sucesso:
        nota.status = res.get('status', nota.status)
        if res.get('numero'):
            nota.numero_nota = str(res.get('numero'))
        if res.get('url_danfe'):
            nota.url_danfe_pdf = res.get('url_danfe')
        if res.get('url_xml'):
            nota.url_xml = res.get('url_xml')
        if res.get('mensagem'):
            nota.mensagem_sefaz = res.get('mensagem')
        db.session.commit()
        flash(f"Status da nota atualizado: {nota.status}", "info")
    else:
        flash(f"Erro ao consultar Focus NFe: {res}", "error")

    next_url = request.form.get('next') or request.referrer or url_for('ver_os', os_id=nota.ordem_servico_id)
    return redirect(next_url)


# --- CENTRAL DA CONTABILIDADE: EXPORTAÇÃO DE XMLs EM LOTE (.ZIP) ---

@app.route('/fiscal/exportar-mes')
@login_required
def fiscal_exportar_mes():
    empresa = g.empresa
    mes_param = request.args.get('mes', '').strip() # formato 'YYYY-MM'
    
    if not mes_param:
        hoje = date.today()
        mes_param = f"{hoje.year}-{hoje.month:02d}"

    try:
        ano, mes = [int(x) for x in mes_param.split('-')]
        data_ini = date(ano, mes, 1)
        if mes == 12:
            data_fim = date(ano + 1, 1, 1) - timedelta(days=1)
        else:
            data_fim = date(ano, mes + 1, 1) - timedelta(days=1)
    except Exception:
        flash("Formato de mês inválido. Use AAAA-MM.", "error")
        return redirect(url_for('configuracoes_fiscal'))

    notas = NotaFiscal.query.filter(
        NotaFiscal.empresa_id == empresa.id,
        NotaFiscal.data_emissao >= data_ini,
        NotaFiscal.data_emissao <= data_fim
    ).order_by(NotaFiscal.data_emissao.asc(), NotaFiscal.id.asc()).all()

    if not notas:
        flash(f"Nenhuma nota fiscal encontrada no período {data_ini.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}.", "warning")
        return redirect(url_for('configuracoes_fiscal'))

    import requests
    zip_buffer = BytesIO()
    csv_output = StringIO()
    csv_writer = csv.writer(csv_output, delimiter=';')
    csv_writer.writerow(['NUMERO', 'SERIE', 'TIPO', 'DATA_EMISSAO', 'VALOR_TOTAL', 'STATUS', 'REFERENCIA', 'CHAVE_ACESSO', 'URL_XML', 'URL_DANFE'])

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for n in notas:
            csv_writer.writerow([
                n.numero_nota or '',
                n.serie_nota or '',
                n.tipo_nota,
                n.data_emissao.strftime('%d/%m/%Y') if n.data_emissao else '',
                f"{float(n.valor_total or 0.0):.2f}".replace('.', ','),
                n.status,
                n.referencia_uuid,
                n.chave_acesso or '',
                n.url_xml or '',
                n.url_danfe_pdf or ''
            ])

            if n.url_xml and n.url_xml.startswith('http'):
                try:
                    r = requests.get(n.url_xml, timeout=10)
                    if r.status_code == 200 and r.content:
                        xml_name = f"xmls/{n.tipo_nota}_{n.numero_nota or n.referencia_uuid}.xml"
                        zip_file.writestr(xml_name, r.content)
                except Exception:
                    pass

        zip_file.writestr(f"relatorio_fiscal_{mes_param}.csv", csv_output.getvalue().encode('utf-8-sig'))

    zip_buffer.seek(0)
    nome_download = f"fechamento_fiscal_{empresa.nome_empresa.replace(' ', '_')}_{mes_param}.zip"
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name=nome_download
    )


if __name__ == '__main__':
    app.run(debug=True, port=5000)